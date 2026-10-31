"""M2 basic E2E runner (E1-E6).

  uv run --env-file ../.env python -m app.m2.run prd --input <input.md> --out <fixture dir>
  uv run --env-file ../.env python -m app.m2.run run --prd <prd.json> [--until spec|plan|code|verify]
  uv run --env-file ../.env python -m app.m2.run run --resume <run dir>
  uv run python -m app.m2.run judge --run-dir <run dir>                                 # no Docker, no model
  uv run python -m app.m2.run manual-review --run-dir <run dir> --input <review.json>   # no Docker, no model
  uv run --env-file ../.env python -m app.m2.run diagnose --run-dir <run dir>           # paid: one Opus call

Every paid call checks the run budget first. Nothing is retried automatically (#11)."""

import argparse
import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.llm import claude
from app.llm.errors import AnalysisError
from pydantic import ValidationError

from app.m2 import acceptance, agent, stages, verify
from app.m2.context import build_context
from app.m2.contracts import (PRD, ManualReview, ManualReviewSubmission, Plan, Spec, check_generated_prd,
                              check_manual_submission, check_prd, order_tasks)

STAGES = ("spec", "plan", "code", "verify")
WORKSPACE_ROOT = Path(os.getenv("HARNESS_WORKSPACE_ROOT") or Path.home() / "dev/2026-itda-project/harness-workspaces")


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def dump(path, data):
    # Write a temporary file, then rename it over the old one: a process that ends mid-write never
    # leaves a half-written run.json. The name is per thread, as the web server writes from several.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def load(path):
    return json.loads(path.read_text())


def read_json(path, unreadable=None):
    """A record that may be missing or damaged: None if missing, `unreadable` if it is not JSON."""
    if not path.exists():
        return None
    try:
        return load(path)
    except ValueError:
        return unreadable


# Characters of diagnosis input; never more tokens than this, so the reservation below holds.
DIAGNOSIS_MAX_INPUT = 60000


class ManualReviewRejected(ValueError):
    """A submission that was refused and saved to manual-review.rejected.json."""


# ---------- verdict (#10): evidence files -> acceptance.build_verification ----------

def judge_run(run_dir, tasks, with_payload=False):
    """Judge from the files as they are now, writing nothing. Raises ValueError or OSError when the
    Spec or Plan the evidence is judged against cannot be read."""
    art = Path(run_dir) / "artifacts"
    record = read_json(art / "verify.json")
    record = record if isinstance(record, dict) else None
    # A damaged review must count as invalid, not as "none yet": the marker fails the contract.
    review = read_json(art / "manual-review.json", unreadable={"unreadable": True})
    spec, plan = Spec.model_validate(load(art / "spec.json")), Plan.model_validate(load(art / "plan.json"))
    verification = acceptance.build_verification(PRD.model_validate(load(art / "prd.json")), spec, plan, record, review,
                                                 tasks, sha((art / "spec.json").read_text()), now())
    payload = acceptance.diagnosis_payload(verification, spec, plan, record, tasks)
    acceptance.attach_diagnosis(verification, read_json(art / "diagnosis.json"), acceptance.evidence_hash(payload))
    return (verification, payload) if with_payload else verification


def diagnose(run_dir, state, budget, requested=False):
    """Ask Opus why a run failed, after the verdict is saved. Whatever goes wrong here is recorded in
    diagnosis.json and raises nothing: a missing diagnosis must not hide or change the verdict. The
    same evidence is never sent twice by itself, and a finished diagnosis not even on request."""
    art = Path(run_dir) / "artifacts"
    verification, payload = judge_run(run_dir, state["tasks"], with_payload=True)
    evidence_sha = acceptance.evidence_hash(payload)
    stored = read_json(art / "diagnosis.json")
    if stored and stored.get("evidence_sha256") == evidence_sha and (stored.get("status") == "done" or not requested):
        return verification
    log = state["calls"]
    calls = len(log)
    result = {"status": "failed", "summary": None, "failures": [], "replan_scope": None, "error": None}
    try:
        if len(json.dumps(payload, ensure_ascii=False)) > DIAGNOSIS_MAX_INPUT:  # the budget reserves this much
            raise ValueError(f"input is larger than the {DIAGNOSIS_MAX_INPUT} reserved")
        budget.reserve("opus", max_output=stages.DIAGNOSIS_MAX_TOKENS, max_input=DIAGNOSIS_MAX_INPUT)
        result.update(status="done", **stages.generate_diagnosis(payload, log).model_dump())
    except SystemExit as exc:  # the budget stops the CLI this way; here the verdict still stands
        result.update(status="budget_stopped", error=str(exc.code))
    except stages.ContractError as exc:
        dump(art / "diagnosis.rejected.json", {"errors": exc.errors, "data": exc.data})
        result.update(status="rejected", error="; ".join(exc.errors))
    except (AnalysisError, ValueError, KeyError) as exc:
        result["error"] = f"{exc.code}: {exc.message}" if isinstance(exc, AnalysisError) else f"{type(exc).__name__}: {exc}"
    dump(art / "diagnosis.json", {**result, "evidence_sha256": evidence_sha, "diagnosed_at": now(),
                                  "call": log[-1] if len(log) > calls else None})
    verification = judge_run(run_dir, state["tasks"])
    dump(art / "verification.json", verification)
    return verification


def save_verdict(run_dir):
    """Judge again and record it, for a finished run: its run.json has no other writer now."""
    state = read_json(Path(run_dir) / "run.json") or {}
    verification = judge_run(run_dir, state.get("tasks"))
    dump(Path(run_dir) / "artifacts/verification.json", verification)
    dump(Path(run_dir) / "run.json", {**state, "final_status": verification["final_status"], "updated_at": now()})
    return verification


def retire_verification(art):
    """Before checking again: a verification that gets interrupted must not leave the previous verdict
    or its evidence standing for code that is being checked anew. The last evidence stays readable."""
    (art / "verification.json").unlink(missing_ok=True)
    if (art / "verify.json").exists():
        os.replace(art / "verify.json", art / "verify.previous.json")


def submit_manual_review(run_dir, data):
    """Validate a person's review and merge it step by step; a partial one is allowed. Refused input
    goes to manual-review.rejected.json and every accepted one to manual-review.history.jsonl."""
    art = Path(run_dir) / "artifacts"
    spec = Spec.model_validate(load(art / "spec.json"))
    spec_sha = sha((art / "spec.json").read_text())
    record = read_json(art / "verify.json")
    record = record if isinstance(record, dict) else {}
    target = art / "manual-review.json"
    existing = read_json(target, unreadable={"unreadable": True})
    errors, submission = [], None
    try:
        submission = ManualReviewSubmission.model_validate(data)
        errors = check_manual_submission(submission, spec)
    except ValidationError as exc:
        errors = [e["msg"] + " at " + ".".join(map(str, e["loc"])) for e in exc.errors()]
    if record.get("status") not in ("manual_pending", "checks_failed"):
        errors.append(f"verification {record.get('status')} gave no running app to review")
    elif not record.get("code_commit") or record.get("spec_sha256") != spec_sha:
        errors.append("verify.json does not say which code and Spec it checked; verify again first")
    try:
        previous = ManualReview.model_validate(existing).items if existing is not None else []
    except ValidationError:
        previous = []
        errors.append("the stored manual-review.json fails the contract; move it away first")
    if errors:
        dump(art / "manual-review.rejected.json", {"rejected_at": now(), "errors": errors, "data": data})
        raise ManualReviewRejected("수동 확인 기록이 거부되었습니다: " + "; ".join(errors))
    at = now()
    new = [{**e.model_dump(), "reviewer": submission.reviewer, "reviewed_at": at,
            "code_commit": record["code_commit"], "spec_sha256": spec_sha} for e in submission.items]
    keys = {(e["check_id"], e["step_index"]) for e in new}
    replaced = [i.model_dump() for i in previous if (i.check_id, i.step_index) in keys]
    kept = [i.model_dump() for i in previous if (i.check_id, i.step_index) not in keys]
    with (art / "manual-review.history.jsonl").open("a") as history:
        history.write(json.dumps({"submitted_at": at, "reviewer": submission.reviewer, "items": new,
                                  "replaced": replaced}, ensure_ascii=False) + "\n")
    dump(target, {"schema_version": 1, "reviewer": submission.reviewer, "reviewed_at": at, "items": kept + new})
    return save_verdict(run_dir)


class Budget:
    """Pre-call budget check from recorded costs; not a provider-side billing cap.
    `costs` returns every recorded cost of the run, including failed and earlier attempts."""

    def __init__(self, limit, costs):
        self.limit, self.costs = limit, costs

    def spent(self):
        costs = self.costs()
        if any(c is None for c in costs):
            raise SystemExit("budget stop: a recorded call has unknown cost; not starting another paid call")
        return sum(costs)

    def check(self, need, what):
        spent = self.spent()
        if spent + need > self.limit:
            raise SystemExit(f"budget stop before {what}: spent {spent:.4f} + reserve {need:.4f} > {self.limit} USD")

    def reserve(self, role, max_output=32000, max_input=60000):
        incoming, outgoing = claude.PRICES[claude.model_for(role)]
        self.check((max_input * incoming + max_output * outgoing) / 1_000_000, role)


def cmd_prd(args):
    out = Path(args.out)
    user_input = Path(args.input).read_text()
    log = []
    Budget(args.budget_usd, lambda: [r.get("cost_usd") for r in log]).reserve("sonnet", max_input=10000)
    try:
        prd = stages.generate_prd(user_input, log)
    except stages.ContractError as exc:
        dump(out / "prd.rejected.json", {"errors": exc.errors, "data": exc.data, "calls": log})
        raise SystemExit(f"PRD rejected: {exc}")
    dump(out / "prd.json", prd.model_dump())
    dump(out / "provenance.json", {
        "kind": "model_generated_fixture",
        "note": "Sonnet이 예시 입력 하나로 생성한 고정 PRD. 요구사항 루프(질문, Review 승인)를 거치지 않았다.",
        "generated_at": now(), "input_file": Path(args.input).name, "input_sha256": sha(user_input),
        "prompt_sha256": sha(stages.prompt("prd_generation") + stages.prompt("stack_contract")),
        "prd_sha256": sha((out / "prd.json").read_text()), "calls": log,
        "contract_check": {"errors": check_generated_prd(prd)}})
    print(f"PRD saved to {out / 'prd.json'}; cost {sum(r.get('cost_usd') or 0 for r in log):.4f} USD")


def cmd_run(args):
    if args.resume:
        run_dir = Path(args.resume)
        state = load(run_dir / "run.json")
        previous_error = state["stages"].pop("error", None)
        if previous_error is not None:
            state.setdefault("previous_errors", []).append(previous_error)
    else:
        run_id = args.run_id or datetime.now().strftime("run-%Y%m%d-%H%M%S")
        run_dir = WORKSPACE_ROOT / run_id
        prd_text = Path(args.prd).read_text()
        agent.init_workspace(run_dir / "app")
        dump(run_dir / "artifacts/prd.json", json.loads(prd_text))
        state = {"run_id": run_id, "started_at": now(), "prd_source": str(Path(args.prd).resolve()),
                 "prd_sha256": sha(prd_text), "models": {"sonnet": claude.model_for("sonnet"),
                                                          "opus": claude.model_for("opus")},
                 "stages": {}, "calls": [], "tasks": {}, "attempts": []}
    art = run_dir / "artifacts"
    app_dir = run_dir / "app"
    log = state["calls"]
    attempts = state.setdefault("attempts", list(state["tasks"].values()))  # older runs kept only the last try
    budget = Budget(args.budget_usd, lambda: [r.get("cost_usd") for r in log]
                    + [a["agent"].get("total_cost_usd") for a in attempts])
    until = STAGES.index(args.until)

    def save():
        msg = [r.get("cost_usd") for r in log]
        agt = [a["agent"].get("total_cost_usd") for a in attempts]
        state["cost_usd"] = {"messages_api": round(sum(c for c in msg if c is not None), 6),
                             "agent": round(sum(c for c in agt if c is not None), 6),
                             "unknown_cost_records": sum(c is None for c in msg + agt)}
        dump(run_dir / "run.json", state)

    save()  # status readers see the run from its first stage, not only after the first task
    prd = PRD.model_validate(load(art / "prd.json"))
    if check_prd(prd):
        raise SystemExit(f"input PRD fails the contract: {check_prd(prd)}")
    try:
        if not (art / "spec.json").exists():
            budget.reserve("opus", max_output=stages.OPUS_MAX_TOKENS)
            spec = stages.generate_spec(prd, log)
            dump(art / "spec.json", spec.model_dump())
            state["stages"]["spec"] = "done"
            save()
        spec = Spec.model_validate(load(art / "spec.json"))
        if until >= 1 and not (art / "plan.json").exists():
            budget.reserve("opus", max_output=stages.OPUS_MAX_TOKENS)
            plan = stages.generate_plan(prd, spec, log)
            dump(art / "plan.json", plan.model_dump())
            state["stages"]["plan"] = "done"
            save()
        if until >= 2:
            plan = Plan.model_validate(load(art / "plan.json"))
            state["task_order"] = order_tasks(plan)
            save()
            tasks = {t.id: t for t in plan.tasks}
            for tid in state["task_order"]:
                if state["tasks"].get(tid, {}).get("status") == "done":
                    continue
                budget.check(args.task_budget_usd, tid)
                done = {k: {"task_id": k, "title": v["title"], "commit": v["commit"], "files": v["files"],
                            "attempt": v.get("attempt", 1), "summary": v["agent"].get("result")}
                        for k, v in state["tasks"].items()
                        if v["status"] == "done"}
                context = build_context(tasks[tid], prd, spec, app_dir, done, agent.SANDBOX)
                dump(art / f"contexts/{tid}.json", context)
                record = agent.run_task(tasks[tid], context, app_dir, args.task_budget_usd)
                record["attempt"] = sum(a["task_id"] == tid for a in attempts) + 1
                attempts.append(record)
                state["tasks"][tid] = record
                name = f"{tid}-attempt-{record['attempt']}"
                dump(art / f"agent/{name}.json", record)
                (art / f"diffs/{name}.diff").parent.mkdir(parents=True, exist_ok=True)
                (art / f"diffs/{name}.diff").write_text(agent.git(app_dir, "diff", record["base_commit"], record["commit"]))
                save()
                if record.get("cleanup_error"):  # the attempt and its cost are saved; nothing more starts
                    state["stages"]["code"] = f"cleanup failed after {tid}"
                    raise SystemExit(f"sandbox cleanup failed after {tid}: {record['cleanup_error']}")
                if record["status"] != "done":
                    state["stages"]["code"] = f"failed at {tid}"
                    raise SystemExit(f"{tid} failed; see {art / f'agent/{name}.json'}")
            state["stages"]["code"] = "done"
        if until >= 3:
            state["stages"].pop("verify", None)  # a resumed run checks the same code again
            def progress(phase):  # lets a status reader tell building from checking
                state["verify_phase"] = phase
                save()
            state.pop("final_status", None)
            # A retry promises to rebuild and check only, so only the first verification may call the model.
            first_verification = not ((art / "verify.json").exists() or (art / "verify.previous.json").exists())
            retire_verification(art)
            evidence_of = {"code_commit": agent.git(app_dir, "rev-parse", "HEAD").strip(),
                           "spec_sha256": sha((art / "spec.json").read_text())}
            result = verify.verify(app_dir, spec, f"harness-{state['run_id']}", keep_running=args.keep_running,
                                   progress=progress)
            dump(art / "verify.json", {**result, **evidence_of})
            state["stages"]["verify"] = result["status"]
            verification = judge_run(run_dir, state["tasks"])
            dump(art / "verification.json", verification)
            state["final_status"] = verification["final_status"]
            print(f"final status: {verification['final_status']}")
            save()  # the verdict is on record before any paid call
            if first_verification and verification["final_status"] in acceptance.AUTO_DIAGNOSIS:
                print(f"diagnosis: {diagnose(run_dir, state, budget)['diagnosis']['status']}")
            print(f"verify: {result['status']} ({result.get('passed')}/{result.get('total')} checks)"
                  + (f", preview {result['preview_url']}" if result.get("preview_url") else ""))
            if result.get("cleanup_error"):  # the next task or verification checks again before it starts
                print(f"warning: sandbox cleanup failed after verify: {result['cleanup_error']}")
    except stages.ContractError as exc:
        dump(art / f"{exc.stage}.rejected.json", {"errors": exc.errors, "data": exc.data})
        state["stages"][exc.stage] = "rejected"
        raise SystemExit(f"{exc.stage} rejected; see {art / f'{exc.stage}.rejected.json'}")
    except AnalysisError as exc:
        state["stages"]["error"] = f"{exc.code}: {exc.message}"
        raise SystemExit(f"model call failed: {exc.code} {exc.message}")
    except agent.CleanupError as exc:  # raised before an agent starts, so no paid call is lost
        state["stages"]["error"] = f"cleanup_failed: {exc}"
        raise SystemExit(f"sandbox cleanup failed; not starting the next task: {exc}")
    finally:
        state["updated_at"] = now()
        save()
    print(f"run dir: {run_dir}\ncost: {state['cost_usd']}")


def finished_run_dir(args):
    run_dir = Path(args.run_dir)
    if (read_json(run_dir / "web.json") or {}).get("status") == "running":
        raise SystemExit("this run is still running; its records cannot change now")
    return run_dir


def cmd_judge(args):
    verification = save_verdict(finished_run_dir(args))
    print(f"final status: {verification['final_status']}\n" + "\n".join(verification["reasons"]))


def cmd_manual_review(args):
    run_dir = finished_run_dir(args)
    try:
        verification = submit_manual_review(run_dir, json.loads(Path(args.input).read_text()))
    except ManualReviewRejected as exc:
        raise SystemExit(str(exc))
    print(f"final status: {verification['final_status']}\n" + "\n".join(verification["reasons"]))


def cmd_diagnose(args):
    """The explicit, paid path: a manual failure and a retry of a failed diagnosis come here."""
    run_dir = finished_run_dir(args)
    state = load(run_dir / "run.json")
    state.setdefault("calls", [])
    attempts = state.get("attempts", [])
    log = state["calls"]
    budget = Budget(args.budget_usd, lambda: [r.get("cost_usd") for r in log] + [a["agent"].get("total_cost_usd") for a in attempts])
    verification, payload = judge_run(run_dir, state.get("tasks"), with_payload=True)
    if verification["final_status"] in acceptance.NOTHING_TO_DIAGNOSE:
        raise SystemExit(f"nothing to diagnose: {verification['final_status']}")
    if not payload["failure_targets"]:
        raise SystemExit("no failure evidence to diagnose; fix the environment and verify again")
    state["tasks"] = state.get("tasks") or {}
    try:
        verification = diagnose(run_dir, state, budget, requested=True)
    finally:
        dump(run_dir / "run.json", {**state, "final_status": verification["final_status"], "updated_at": now()})
    status = verification["diagnosis"]["status"]
    if status != "done":
        raise SystemExit(f"diagnosis {status}: {verification['diagnosis'].get('error')}")
    print(f"diagnosis: {status}, replan_scope {verification['diagnosis']['replan_scope']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prd")
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--budget-usd", type=float, default=0.5)
    r = sub.add_parser("run")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--prd")
    src.add_argument("--resume")
    r.add_argument("--run-id")
    r.add_argument("--until", choices=STAGES, default="verify")
    r.add_argument("--budget-usd", type=float, default=8.0)
    r.add_argument("--task-budget-usd", type=float, default=1.5)
    r.add_argument("--keep-running", action="store_true")
    j = sub.add_parser("judge")
    j.add_argument("--run-dir", required=True)
    m = sub.add_parser("manual-review")
    m.add_argument("--run-dir", required=True)
    m.add_argument("--input", required=True)
    d = sub.add_parser("diagnose")
    d.add_argument("--run-dir", required=True)
    d.add_argument("--budget-usd", type=float, default=8.0)
    args = parser.parse_args(argv)
    {"prd": cmd_prd, "run": cmd_run, "judge": cmd_judge, "manual-review": cmd_manual_review,
     "diagnose": cmd_diagnose}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

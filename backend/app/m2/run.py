"""M2 basic E2E runner (E1-E6).

  uv run --env-file ../.env python -m app.m2.run prd --input <input.md> --out <fixture dir>
  uv run --env-file ../.env python -m app.m2.run run --prd <prd.json> [--until spec|plan|code|verify]
  uv run --env-file ../.env python -m app.m2.run run --resume <run dir>

Every paid call checks the run budget first. A failed verification gets up to --max-repairs
rounds of Opus repair plan -> Sonnet fix -> verification (#11); nothing else is retried."""

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
from app.m2 import agent, stages, verify
from app.m2.context import build_context
from app.m2.contracts import PRD, Plan, Spec, check_generated_prd, check_prd, order_tasks

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


REPAIRABLE = ("build_failed", "checks_failed")


def repair_stop(repairs, result, limit):
    """Why the repair loop must not start another round, or None."""
    if len(repairs) >= limit:
        return "limit_reached"
    last = repairs[-1] if repairs else None
    if last and last["result"] != "rolled_back" and sorted(last["failures"]) == sorted(failure_list(result)):
        return "same_failures"
    return None


def worse(before, after):
    """A repair made things worse if the app now fails more checks, or no longer builds."""
    if after["status"] not in REPAIRABLE:
        return False
    if after["status"] == "build_failed" and before["status"] != "build_failed":
        return True
    return len(failure_list(after)) > len(failure_list(before))


def failure_list(result):
    return result.get("failures") or [result["status"]]


def failure_report(result, spec):
    """The evidence the repair planner sees: failed checks with their HTTP results and log tails."""
    failed = [c for c in result.get("checks", []) + result.get("checks_after_restart", []) if not c["passed"]]
    return {"status": result["status"], "failures": failure_list(result), "failed_checks": failed,
            "build_log_tail": result.get("build", {}).get("log_tail", ""),
            "server_log_tail": result.get("server_log_tail", "")}


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

    prd = PRD.model_validate(load(art / "prd.json"))
    if check_prd(prd):
        raise SystemExit(f"input PRD fails the contract: {check_prd(prd)}")
    try:
        if not (art / "spec.json").exists():
            budget.reserve("opus", max_output=stages.OPUS_MAX_TOKENS)
            spec = stages.generate_spec(prd, log)
            dump(art / "spec.json", spec.model_dump())
            state["stages"]["spec"] = "done"
        spec = Spec.model_validate(load(art / "spec.json"))
        if until >= 1 and not (art / "plan.json").exists():
            budget.reserve("opus", max_output=stages.OPUS_MAX_TOKENS)
            plan = stages.generate_plan(prd, spec, log)
            dump(art / "plan.json", plan.model_dump())
            state["stages"]["plan"] = "done"
        if until >= 2:
            plan = Plan.model_validate(load(art / "plan.json"))
            state["task_order"] = order_tasks(plan)
            tasks = {t.id: t for t in plan.tasks}

            def attempt(tid, last_error=None):
                budget.check(args.task_budget_usd, tid)
                done = {k: {"task_id": k, "title": v["title"], "commit": v["commit"], "files": v["files"],
                            "attempt": v.get("attempt", 1), "summary": v["agent"].get("result")}
                        for k, v in state["tasks"].items()
                        if v["status"] == "done"}
                n = sum(a["task_id"] == tid for a in attempts) + 1
                context = build_context(tasks[tid], prd, spec, app_dir, done, agent.SANDBOX, last_error)
                dump(art / f"contexts/{tid}.json" if n == 1 else art / f"contexts/{tid}-attempt-{n}.json", context)
                record = agent.run_task(tasks[tid], context, app_dir, args.task_budget_usd)
                record["attempt"] = n
                attempts.append(record)
                state["tasks"][tid] = record
                name = f"{tid}-attempt-{n}"
                dump(art / f"agent/{name}.json", record)
                (art / f"diffs/{name}.diff").parent.mkdir(parents=True, exist_ok=True)
                (art / f"diffs/{name}.diff").write_text(agent.git(app_dir, "diff", record["base_commit"], record["commit"]))
                save()
                if record.get("cleanup_error"):  # the attempt and its cost are saved; nothing more starts
                    state["stages"]["code"] = f"cleanup failed after {tid}"
                    raise SystemExit(f"sandbox cleanup failed after {tid}: {record['cleanup_error']}")
                return record, name

            for tid in state["task_order"]:
                if state["tasks"].get(tid, {}).get("status") == "done":
                    continue
                record, name = attempt(tid)
                if record["status"] != "done":
                    state["stages"]["code"] = f"failed at {tid}"
                    raise SystemExit(f"{tid} failed; see {art / f'agent/{name}.json'}")
            state["stages"]["code"] = "done"
        if until >= 3:
            def check_app(path):
                result = verify.verify(app_dir, spec, f"harness-{state['run_id']}", keep_running=args.keep_running)
                dump(art / path, result)
                state["stages"]["verify"] = result["status"]
                print(f"verify: {result['status']} ({result.get('passed')}/{result.get('total')} checks)"
                      + (f", preview {result['preview_url']}" if result.get("preview_url") else ""))
                if result.get("cleanup_error"):  # the next task or verification checks again before it starts
                    print(f"warning: sandbox cleanup failed after verify: {result['cleanup_error']}")
                return result

            result = check_app("verify.json")
            repairs = state.setdefault("repairs", [])
            while result["status"] in REPAIRABLE:
                if stop := repair_stop(repairs, result, args.max_repairs):
                    state["stages"]["repair"] = stop
                    break
                rnd = len(repairs) + 1
                failure = failure_report(result, spec)
                try:
                    budget.reserve("opus")
                except SystemExit:
                    state["stages"]["repair"] = "budget_stop"
                    raise
                try:
                    fix = stages.generate_repair_plan(prd, spec, plan, failure, repairs, log)
                except stages.ContractError as exc:
                    dump(art / f"repair/round-{rnd}.plan.rejected.json", {"errors": exc.errors, "data": exc.data})
                    state["stages"]["repair"] = "plan_rejected"
                    break
                dump(art / f"repair/round-{rnd}.plan.json", fix.model_dump())
                before = result
                entry = {"round": rnd, "failures": failure["failures"], "diagnosis": fix.diagnosis,
                         "plan": f"repair/round-{rnd}.plan.json", "tasks": [s.task_id for s in fix.steps],
                         "base_commit": agent.git(app_dir, "rev-parse", "HEAD").strip(),
                         "attempts": [], "verify": None, "result": None}
                repairs.append(entry)
                for step in fix.steps:
                    note = {"repair_round": rnd, "diagnosis": fix.diagnosis, "instructions": step.instructions,
                            "check_ids": step.check_ids, "failure": failure}
                    try:
                        record, name = attempt(step.task_id, note)
                    except SystemExit:
                        state["stages"]["repair"] = "budget_stop"
                        raise
                    entry["attempts"].append(name)
                    if record["status"] != "done":
                        entry["result"] = state["stages"]["repair"] = "fix_failed"
                        raise SystemExit(f"repair round {rnd}: {step.task_id} failed; see {art / f'agent/{name}.json'}")
                entry["verify"] = f"repair/round-{rnd}.verify.json"
                result = check_app(entry["verify"])
                entry["result"] = result["status"]
                entry["failures_after"] = failure_list(result) if result["status"] in REPAIRABLE else []
                if worse(before, result):
                    # Keep the failed round in the history, but continue from the code before it.
                    entry["result"], entry["rolled_back_to"] = "rolled_back", entry["base_commit"]
                    agent.rollback(app_dir, entry["base_commit"], f"revert repair round {rnd}: more failures")
                    result = before
                    state["stages"]["verify"] = result["status"]
                save()
            else:
                if repairs:
                    state["stages"]["repair"] = result["status"]
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
    r.add_argument("--max-repairs", type=int, default=3)
    args = parser.parse_args(argv)
    (cmd_prd if args.cmd == "prd" else cmd_run)(args)


if __name__ == "__main__":
    sys.exit(main())

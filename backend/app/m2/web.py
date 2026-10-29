"""Web entry points for M2: Review state -> one "generate" request -> Sonnet PRD -> M2 run (existing
`run.cmd_run`), all in one background thread.

Local demo scope: runs are threads of this one server process; state lives in files under the
workspace root. A server restart turns an unfinished run into `interrupted`; the user can resume it."""

import hashlib
import json
import os
import re
import subprocess
import threading

from app.llm.errors import AnalysisError
from app.m2 import run, stages
from app.m2.contracts import ReviewedPRD
from app.schemas import WorkflowState
from app.workflow import approve, require_phase, validate_state

PRD_BUDGET_USD = float(os.getenv("M2_PRD_BUDGET_USD") or 0.5)
RUN_BUDGET_USD = float(os.getenv("M2_RUN_BUDGET_USD") or 8.0)
TASK_BUDGET_USD = float(os.getenv("M2_TASK_BUDGET_USD") or 1.5)
ID = re.compile(r"[0-9a-f]{16}")

_lock = threading.Lock()
_prd_jobs: set[str] = set()
_threads: dict[str, threading.Thread] = {}


class Conflict(Exception):
    """Request is valid but cannot run now (in progress, needs confirmation, another run active)."""


def prd_dir(prd_id):
    if not ID.fullmatch(prd_id):
        raise ValueError("알 수 없는 PRD입니다.")
    return run.WORKSPACE_ROOT / "prds" / prd_id


def run_dir(run_id):
    if not (run_id.startswith("web-") and ID.fullmatch(run_id[4:])):
        raise ValueError("알 수 없는 실행입니다.")
    return run.WORKSPACE_ROOT / run_id


def read(path, default=None):
    return run.load(path) if path.exists() else default


# ---------- Review -> PRD ----------

def generate_prd(state):
    """Sonnet PRD from an approved WorkflowState. The handoff is rebuilt on the server from the state,
    so the PRD input is exactly what Review approved. Same approved state -> same PRD id, no second call."""
    require_phase(state, "approved")
    validate_state(state)
    _, reviewed = approve(state)
    prd_id = prd_id_of(state)
    out = prd_dir(prd_id)
    with _lock:
        if (out / "prd.json").exists():
            return prd_view(prd_id)
        if prd_id in _prd_jobs:
            raise Conflict("같은 Review 결과로 PRD를 생성하고 있습니다.")
        _prd_jobs.add(prd_id)
    try:
        previous = list(out.glob("prd.rejected-*.json")) + list(out.glob("prd.failed-*.json"))
        attempt = len(previous) + 1
        # earlier billed attempts count too, so an unknown-cost failure blocks the next paid call
        earlier = [c.get("cost_usd") for path in previous for c in run.load(path).get("calls", [])]
        run.dump(out / "reviewed.json", reviewed.model_dump())
        log = []
        source = {"analysis_project_id": state.project_id, "analysis_run_id": state.run_id,
                  "analysis_mode": state.analysis_mode, "requirement_loop_usage": state.usage,
                  "input_text_sha256": run.sha(state.text)}
        try:
            run.Budget(PRD_BUDGET_USD, lambda: earlier + [r.get("cost_usd") for r in log]).reserve("sonnet", max_input=20000)
        except SystemExit as exc:  # the shared budget check stops the CLI; here it is a refused request
            raise Conflict(str(exc.code)) from None
        try:
            prd = stages.generate_reviewed_prd(state.text, state.history, reviewed, log)
        except AnalysisError as exc:  # keep the record of a billed call even when it fails
            run.dump(out / f"prd.failed-{attempt}.json", {"error": exc.code, "calls": log, **source})
            raise
        except stages.ContractError as exc:
            run.dump(out / f"prd.rejected-{attempt}.json",
                     {"errors": exc.errors, "data": exc.data, "calls": log, **source})
            return {"prd_id": prd_id, "status": "rejected", "errors": exc.errors, "calls": log}
        run.dump(out / "prd.json", prd.model_dump())
        run.dump(out / "provenance.json", {
            "kind": "reviewed_prd", "generated_at": run.now(), "prd_sha256": run.sha((out / "prd.json").read_text()),
            "prompt_sha256": run.sha(stages.prompt("reviewed_prd_generation") + stages.prompt("stack_contract")),
            "calls": log, **source})
        return prd_view(prd_id)
    finally:
        with _lock:
            _prd_jobs.discard(prd_id)


def prd_id_of(state):
    return hashlib.sha256(state.model_dump_json().encode()).hexdigest()[:16]


def prd_view(prd_id):
    out = prd_dir(prd_id)
    prd = ReviewedPRD.model_validate(run.load(out / "prd.json"))
    provenance = run.load(out / "provenance.json")
    return {"prd_id": prd_id, "status": "passed", "errors": [], "prd": prd.model_dump(),
            "open_issues": [i.model_dump() for i in prd.open_issues], "calls": provenance["calls"],
            "run_id": f"web-{prd_id}" if run_dir(f"web-{prd_id}").exists() else None}


# ---------- one "generate" request: Review state -> PRD -> M2 run ----------

def start_generation(state):
    """The single generate button. Approves the Review state, then a background thread makes the PRD
    and starts the runner. The run id comes from the approved state, so a repeated click or resent
    request returns the same run instead of starting a second paid one."""
    if state.phase == "review":
        state, _ = approve(state)
    require_phase(state, "approved")
    validate_state(state)
    prd_id = prd_id_of(state)
    run_id = f"web-{prd_id}"
    target = run_dir(run_id)
    with _lock:
        if target.exists():
            return {"run_id": run_id, "created": False}
        if any(t.is_alive() for t in _threads.values()):
            raise Conflict("다른 MVP 생성이 진행 중입니다.")
        # the inputs of this service (original text, answers, reviewed requirements) stay with its PRD
        run.dump(prd_dir(prd_id) / "state.json", state.model_dump())
        run.dump(target / "web.json", {
            "status": "running", "stage": "prd", "prd_id": prd_id, "started_at": run.now(),
            "state": f"prds/{prd_id}/state.json", "prd_provenance": f"prds/{prd_id}/provenance.json",
            "budget_usd": RUN_BUDGET_USD, "task_budget_usd": TASK_BUDGET_USD})
        return _spawn(run_id, _generate, created=True)


def _generate(run_id):
    web = run_dir(run_id) / "web.json"
    prd_id = run_id[4:]
    out = prd_dir(prd_id)
    try:
        if not (out / "prd.json").exists():
            result = generate_prd(WorkflowState.model_validate(run.load(out / "state.json")))
            if result["status"] != "passed":
                return _finish(web, "failed", "요구사항 정리 결과가 검증을 통과하지 못했습니다. "
                                              f"({len(result['errors'])}건, prds/{prd_id}/prd.rejected-*.json)")
        prd = ReviewedPRD.model_validate(run.load(out / "prd.json"))
        if prd.open_issues and not run.load(web).get("open_issues_confirmed"):
            return _finish(web, "awaiting_confirmation", None)
    except (AnalysisError, Conflict) as exc:
        return _finish(web, "failed", str(exc))
    except Exception as exc:  # never leave a dead run looking like it is still running
        return _finish(web, "failed", f"{type(exc).__name__}: {exc}")
    run.dump(web, {**run.load(web), "stage": "run"})
    _run(run_id, ["--prd", str(out / "prd.json"), "--run-id", run_id])


def _finish(web, status, error):
    run.dump(web, {**run.load(web), "status": status, "error": error, "ended_at": run.now()})


# ---------- PRD -> M2 run ----------

def start_run(prd_id, confirm_open_issues, retry=False):
    """Continue or start the M2 runner for a stored PRD. One run per PRD (repeat requests return it)
    and one active run per server, so a double click never starts a second paid run.
    - a run waiting on open issues continues once the user confirms them
    - `retry` resumes a failed or interrupted run (PRD step again if it stopped there, else the
      runner's --resume), keeping its earlier attempts and costs."""
    out = prd_dir(prd_id)
    run_id = f"web-{prd_id}"
    target = run_dir(run_id)
    with _lock:
        if any(t.is_alive() for t in _threads.values()):
            if target.exists():
                return {"run_id": run_id, "created": False}
            raise Conflict("다른 MVP 생성이 진행 중입니다.")
        if target.exists():
            web = run.load(target / "web.json")
            if web["status"] == "awaiting_confirmation":
                if not confirm_open_issues:
                    raise Conflict("미결정 사항을 확인한 뒤 다시 요청하세요.")
                run.dump(target / "web.json", {**web, "status": "running", "stage": "run", "error": None,
                                               "ended_at": None, "open_issues_confirmed": True})
                return _start(run_id, ["--prd", str(out / "prd.json"), "--run-id", run_id], created=False)
            if not retry or not (web["status"] in ("failed", "interrupted") or verification_failed(target, web)):
                return {"run_id": run_id, "created": False}
            resumed = {**web, "status": "running", "error": None, "ended_at": None, "resumed_at": run.now(),
                       "budget_usd": RUN_BUDGET_USD, "task_budget_usd": TASK_BUDGET_USD,
                       "previous_errors": [*web.get("previous_errors", []), web.get("error")]}
            if not (target / "run.json").exists():
                if web.get("stage") != "prd":
                    raise Conflict("이어서 실행할 기록이 없어 다시 시도할 수 없습니다.")
                run.dump(target / "web.json", resumed)  # stopped before the runner started: redo that step
                return _spawn(run_id, _generate, created=False)
            run.dump(target / "web.json", resumed)
            return _start(run_id, ["--resume", str(target)], created=False)
        if not (out / "prd.json").exists():
            raise ValueError("검증을 통과한 PRD가 없어 실행할 수 없습니다.")
        prd = ReviewedPRD.model_validate(run.load(out / "prd.json"))
        if prd.open_issues and not confirm_open_issues:
            raise Conflict(f"미결정 사항 {len(prd.open_issues)}건이 있습니다. 확인 후 다시 요청하세요.")
        run.dump(target / "web.json", {
            "status": "running", "stage": "run", "prd_id": prd_id, "started_at": run.now(),
            "open_issues_confirmed": bool(prd.open_issues), "prd_provenance": f"prds/{prd_id}/provenance.json",
            "budget_usd": RUN_BUDGET_USD, "task_budget_usd": TASK_BUDGET_USD})
        return _start(run_id, ["--prd", str(out / "prd.json"), "--run-id", run_id], created=True)


def _start(run_id, source_args, created):
    """Caller holds _lock."""
    return _spawn(run_id, lambda rid: _run(rid, source_args), created)


def _spawn(run_id, target, created):
    """Caller holds _lock."""
    thread = threading.Thread(target=target, args=(run_id,), daemon=True)
    _threads[run_id] = thread
    thread.start()
    return {"run_id": run_id, "created": created}


def _run(run_id, source_args):
    web = run_dir(run_id) / "web.json"
    result = {"status": "finished", "error": None}
    try:
        run.main(["run", *source_args, "--keep-running",
                  "--budget-usd", str(RUN_BUDGET_USD), "--task-budget-usd", str(TASK_BUDGET_USD)])
    except SystemExit as exc:  # the CLI runner reports every stop (budget, rejection, task failure) this way
        if exc.code not in (None, 0):
            result = {"status": "failed", "error": str(exc.code)}
    except Exception as exc:  # never leave a dead run looking like it is still running
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    run.dump(web, {**run.load(web), **result, "ended_at": run.now()})


def reason_for(outcome, error):
    """Short user-facing reason. The raw error (paths, codes) stays in `error` and web.json."""
    error = error or ""
    if outcome == "interrupted":
        return "서버가 다시 시작되어 생성이 멈췄습니다."
    if outcome == "verification_failed":
        return "만든 앱이 빌드 또는 최종 점검을 통과하지 못했습니다."
    if "budget stop" in error or "예산" in error:
        return "이번 생성에 정한 비용 한도에 닿아 멈췄습니다."
    if "검증을 통과하지 못했습니다" in error or "rejected" in error:
        return "AI가 만든 설계가 검증을 통과하지 못했습니다."
    if error.startswith("TASK-"):
        return "기능을 구현하는 중 한 작업이 끝나지 못했습니다."
    if "model call failed" in error or "Claude API" in error:
        return "AI 응답을 끝까지 받지 못했습니다."
    return "생성 중 문제가 생겨 멈췄습니다."


def verification_failed(target, web):
    """A finished run whose build or checks failed. Retrying it runs the same code through build and
    checks again (the runner skips finished stages); nothing is regenerated or repaired."""
    verify = read(target / "artifacts/verify.json")
    return web["status"] == "finished" and not (verify and verify.get("status") in ("passed", "manual_pending"))


def run_status(run_id):
    target = run_dir(run_id)
    web = read(target / "web.json")
    if web is None:
        raise ValueError("알 수 없는 실행입니다.")
    thread = _threads.get(run_id)
    if web["status"] == "running" and not (thread and thread.is_alive()):
        web = {**web, "status": "interrupted", "error": "서버가 재시작되어 실행이 중단되었습니다.", "ended_at": run.now()}
        run.dump(target / "web.json", web)
    state = read(target / "run.json", {})
    art = target / "artifacts"
    verify = read(art / "verify.json")
    stages_done = state.get("stages", {})
    # a stage counts as passed only when it is "done": a failed or rejected one is where a resumed run restarts
    current = "prd" if web.get("stage") == "prd" else next((s for s in run.STAGES if stages_done.get(s) != "done"), None)
    if current == "verify":
        current = state.get("verify_phase", "build")  # build, then check (recorded by the runner)
    tasks = [{"id": tid, "title": t["title"], "status": t["status"], "attempt": t.get("attempt", 1),
              "cost_usd": t["agent"].get("total_cost_usd")} for tid, t in state.get("tasks", {}).items()]
    if web["status"] in ("running", "awaiting_confirmation"):
        outcome = web["status"]
    elif web["status"] in ("failed", "interrupted"):
        outcome = web["status"]
    elif verify and verify.get("status") in ("passed", "manual_pending"):
        # automatic checks only; a human review is still pending. No automatic check is its own outcome.
        outcome = "manual_check_only" if verify.get("automatic_status") == "not_applicable" else "auto_checks_passed"
    else:
        outcome = "verification_failed"
    preview = verify.get("preview_url") if verify else None
    return {
        "run_id": run_id, "prd_id": web["prd_id"], "outcome": outcome, "error": web.get("error"),
        "reason": reason_for(outcome, web.get("error")) if outcome in ("failed", "interrupted", "verification_failed") else None,
        "current_stage": current if outcome == "running" else None, "stages": stages_done,
        "tasks": tasks, "task_order": state.get("task_order", []),
        "verify": {k: verify.get(k) for k in ("status", "automatic_status", "passed", "total", "failures")} if verify else None,
        "manual_review": "done" if (art / "manual-review.json").exists() else "pending",
        "preview_url": preview if preview and preview_running(run_id) else None,
        "cost_usd": costs(state), "started_at": web["started_at"], "ended_at": web.get("ended_at"),
        "models": state.get("models"),
        "open_issues": [i["description"] for i in read(prd_dir(web["prd_id"]) / "prd.json", {}).get("open_issues", [])]
        if outcome == "awaiting_confirmation" else [],
    }


def costs(state):
    """Computed from the call and attempt records, so a cost marked unknown later is never shown as 0."""
    msg = [c.get("cost_usd") for c in state.get("calls", [])]
    agt = [a["agent"].get("total_cost_usd") for a in state.get("attempts", [])]
    return {"messages_api": round(sum(c for c in msg if c is not None), 6),
            "agent": round(sum(c for c in agt if c is not None), 6),
            "unknown_cost_records": sum(c is None for c in msg + agt)}


def container(run_id):
    run_dir(run_id)  # validates the id before it reaches docker
    return f"harness-{run_id}"


def preview_running(run_id):
    out = subprocess.run(["docker", "ps", "-q", "--filter", f"name=^{container(run_id)}$"],
                         capture_output=True, text=True)
    return bool(out.stdout.strip())


def stop_preview(run_id):
    subprocess.run(["docker", "rm", "-f", container(run_id)], capture_output=True)
    return {"run_id": run_id, "preview_running": preview_running(run_id)}

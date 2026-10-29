"""Web entry points for M2: approved Review -> Sonnet PRD -> background M2 run (existing `run.cmd_run`).

Local demo scope: runs are threads of this one server process; state lives in files under the
workspace root. A server restart turns an unfinished run into `interrupted` (no resume from the web)."""

import hashlib
import json
import os
import re
import subprocess
import threading

from app.llm.errors import AnalysisError
from app.m2 import run, stages
from app.m2.contracts import ReviewedPRD
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
    # Keep old states mapped to their existing PRD and failed-call budget records.
    excluded = {"proposal_conversions"} if not state.proposal_conversions else set()
    prd_id = hashlib.sha256(state.model_dump_json(exclude=excluded).encode()).hexdigest()[:16]
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
        if any(c is None for c in earlier):
            raise Conflict("비용 확인이 필요합니다 (unknown cost). 운영자가 이전 PRD 호출 비용을 확인한 뒤 다시 시도하세요.")
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


def prd_view(prd_id):
    out = prd_dir(prd_id)
    prd = ReviewedPRD.model_validate(run.load(out / "prd.json"))
    provenance = run.load(out / "provenance.json")
    return {"prd_id": prd_id, "status": "passed", "errors": [], "prd": prd.model_dump(),
            "open_issues": [i.model_dump() for i in prd.open_issues], "calls": provenance["calls"],
            "run_id": f"web-{prd_id}" if run_dir(f"web-{prd_id}").exists() else None}


# ---------- PRD -> M2 run ----------

def start_run(prd_id, confirm_open_issues, retry=False):
    """Start the existing M2 runner on the stored PRD. One run per PRD (repeat requests return it)
    and one active run per server, so a double click never starts a second paid run. `retry` resumes a
    failed or interrupted run with the runner's --resume, keeping its earlier attempts and costs."""
    out = prd_dir(prd_id)
    if not (out / "prd.json").exists():
        raise ValueError("검증을 통과한 PRD가 없어 실행할 수 없습니다.")
    prd = ReviewedPRD.model_validate(run.load(out / "prd.json"))
    if prd.open_issues and not confirm_open_issues:
        raise Conflict(f"미결정 사항 {len(prd.open_issues)}건이 있습니다. 확인 후 다시 요청하세요.")
    run_id = f"web-{prd_id}"
    target = run_dir(run_id)
    with _lock:
        if any(t.is_alive() for t in _threads.values()):
            if target.exists():
                return {"run_id": run_id, "created": False}
            raise Conflict("다른 MVP 생성이 진행 중입니다.")
        if target.exists():
            web = run.load(target / "web.json")
            if not retry or web["status"] not in ("failed", "interrupted"):
                return {"run_id": run_id, "created": False}
            if not (target / "run.json").exists():
                raise Conflict("이어서 실행할 기록이 없어 다시 시도할 수 없습니다.")
            if costs(run.load(target / "run.json"))["unknown_cost_records"]:
                raise Conflict("비용 확인이 필요합니다. 운영자가 중단된 호출 비용을 확인한 뒤 다시 시도하세요.")
            run.dump(target / "web.json", {
                **web, "status": "running", "error": None, "ended_at": None, "resumed_at": run.now(),
                "budget_usd": RUN_BUDGET_USD, "task_budget_usd": TASK_BUDGET_USD,
                "previous_errors": [*web.get("previous_errors", []), web.get("error")]})
            return _start(run_id, ["--resume", str(target)], created=False)
        run.dump(target / "web.json", {
            "status": "running", "prd_id": prd_id, "started_at": run.now(),
            "open_issues_confirmed": bool(prd.open_issues), "prd_provenance": f"prds/{prd_id}/provenance.json",
            "budget_usd": RUN_BUDGET_USD, "task_budget_usd": TASK_BUDGET_USD})
        return _start(run_id, ["--prd", str(out / "prd.json"), "--run-id", run_id], created=True)


def _start(run_id, source_args, created):
    """Caller holds _lock."""
    thread = threading.Thread(target=_run, args=(run_id, source_args), daemon=True)
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
    current = next((s for s in run.STAGES if s not in stages_done), None)
    tasks = [{"id": tid, "title": t["title"], "status": t["status"], "attempt": t.get("attempt", 1),
              "cost_usd": t["agent"].get("total_cost_usd")} for tid, t in state.get("tasks", {}).items()]
    if web["status"] == "running":
        outcome = "running"
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
        "current_stage": current if outcome == "running" else None, "stages": stages_done,
        "tasks": tasks, "task_order": state.get("task_order", []),
        "verify": {k: verify.get(k) for k in ("status", "automatic_status", "passed", "total", "failures")} if verify else None,
        "manual_review": "done" if (art / "manual-review.json").exists() else "pending",
        "preview_url": preview if preview and preview_running(run_id) else None,
        "cost_usd": costs(state), "started_at": web["started_at"], "ended_at": web.get("ended_at"),
        "models": state.get("models"),
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

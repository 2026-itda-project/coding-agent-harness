"""#10: the final verdict of a run, decided by code from execution evidence only: verify.json, the
Spec and Plan it was planned from, and recorded human reviews. Pure functions, no files or model
calls. No model output is an input here, so none can change a verdict."""

from pydantic import ValidationError

from app.m2.contracts import ManualReview, check_manual_entries

# Most severe first: the first that applies is the run's final status.
FINAL_STATUSES = ("insufficient_evidence", "build_failed", "test_failed", "auto_failed", "manual_failed",
                  "manual_pending", "passed")
# An AC or check takes its worst state. Failure evidence outranks missing evidence, and a pending
# review outranks a pass.
STATES = ("no_evidence", "auto_failed", "manual_failed", "not_run", "manual_pending", "manual_passed", "auto_passed")
ROUTES = {"insufficient_evidence": "environment", "build_failed": "replan_candidate", "test_failed": "replan_candidate",
          "auto_failed": "replan_candidate", "manual_failed": "replan_candidate", "manual_pending": "wait_for_human",
          "passed": "none"}
# Statuses of a verification that ended before it could say anything about the app. A failed
# `docker run -d` is one: the container never started, and an app that crashes shows up as `serve`.
NO_APP_EVIDENCE = {"environment_error": "실행 환경 오류로 검증이 중단되었습니다.",
                   "workspace_error": "작업 폴더를 안전하게 다룰 수 없어 검증이 중단되었습니다.",
                   "start_failed": "앱 컨테이너를 시작하지 못해 점검이 실행되지 않았습니다."}
KNOWN_STATUSES = {*NO_APP_EVIDENCE, "build_failed", "checks_failed", "manual_pending", "passed"}
# A command that ran out of time or was killed: the app, its size or the machine may be the cause.
AMBIGUOUS_OUTCOMES = ("timeout", "killed")
NO_DIAGNOSIS = {"status": "not_implemented", "summary": None, "failures": [], "replan_scope": None, "call": None}


def worst(states):
    return min(states, key=STATES.index)


def read_review(raw, spec, code_commit, spec_sha):
    """The stored review as {(check_id, step_index): item} for the code and Spec under judgment, plus
    the items recorded for other code. A file that fails its contract counts for nothing."""
    if raw is None:
        return {"state": "none", "items": {}, "stale": {}, "errors": []}
    try:
        review = ManualReview.model_validate(raw)
    except ValidationError as exc:
        return {"state": "invalid", "items": {}, "stale": {},
                "errors": [e["msg"] + " at " + ".".join(map(str, e["loc"])) for e in exc.errors()]}
    current = [i for i in review.items if code_commit and i.code_commit == code_commit and i.spec_sha256 == spec_sha]
    errors = check_manual_entries(review.reviewer, current, spec)
    if errors:
        return {"state": "invalid", "items": {}, "stale": {}, "errors": errors}
    key = lambda i: (i.check_id, i.step_index)
    return {"state": "recorded", "items": {key(i): i for i in current},
            "stale": {key(i): i for i in review.items if i not in current}, "errors": []}


def check_rows(spec, plan, record, review):
    """One row per Spec check: both automatic runs and each manual step, from the results themselves."""
    record = record or {}
    first = {c.get("id"): c for c in record.get("checks") or []}
    after = {c.get("id"): c for c in record.get("checks_after_restart") or []}
    rows = []
    for check in spec.checks:
        def run_state(results):
            if check.id not in results:
                return "not_run"
            return "passed" if results[check.id].get("passed") is True else "failed"
        automatic = bool(check.http_steps)
        manual = []
        for index, text in enumerate(check.manual_steps):
            key = (check.id, index)
            item = review["items"].get(key)
            row = {"step_index": index, "step": text, "state": "pending"}
            if item:
                row.update(state="passed" if item.verdict == "pass" else "failed", evidence=item.evidence,
                           reviewer=item.reviewer, reviewed_at=item.reviewed_at)
            elif key in review["stale"]:
                row["state"] = "stale"
            manual.append(row)
        row = {"id": check.id, "ac_ids": check.ac_ids,
               "task_ids": [t.id for t in plan.tasks if check.id in t.check_ids],
               "first_run": run_state(first) if automatic else "not_applicable",
               "after_restart": run_state(after) if automatic and check.rerun_after_restart else "not_applicable",
               "manual": manual}
        row["state"] = check_state(row)
        rows.append(row)
    return rows


def check_state(row):
    runs = [r for r in (row["first_run"], row["after_restart"]) if r != "not_applicable"]
    manual = [m["state"] for m in row["manual"]]
    states = []
    if "failed" in runs:
        states.append("auto_failed")
    if "failed" in manual:
        states.append("manual_failed")
    if "not_run" in runs:
        states.append("not_run")
    if "pending" in manual or "stale" in manual:
        states.append("manual_pending")
    if manual:
        states.append("manual_passed")
    if runs:
        states.append("auto_passed")
    return worst(states) if states else "no_evidence"


def rollup(prd, plan, rows, tasks):
    acceptance = []
    for ac in prd.acceptance_criteria:
        covering = [r for r in rows if ac.id in r["ac_ids"]]
        acceptance.append({"id": ac.id, "state": worst([r["state"] for r in covering]) if covering else "no_evidence",
                           "check_ids": [r["id"] for r in covering],
                           "task_ids": sorted({t for r in covering for t in r["task_ids"]})})
    per_task = []
    for task in plan.tasks:
        record = tasks.get(task.id) or {}
        attempt = record.get("attempt", 1) if record else None
        own = [r["state"] for r in rows if r["id"] in task.check_ids]
        per_task.append({"id": task.id, "agent_status": record.get("status"), "attempt": attempt,
                         "commit": record.get("commit"), "check_ids": task.check_ids,
                         "state": worst(own) if own else "no_checks",
                         "diff": f"diffs/{task.id}-attempt-{attempt}.diff" if record else None})
    return acceptance, per_task


def consistency_errors(prd, spec, plan, record, rows, tasks):
    """Evidence that does not line up with what was planned cannot support any verdict."""
    errors = []
    ids = {c.id for c in spec.checks}
    for name in ("checks", "checks_after_restart"):
        results = [c.get("id") for c in record.get(name) or []]
        errors += [f"{name} has a result for unknown check {i}" for i in results if i not in ids]
        errors += [f"{name} has several results for {i}" for i in sorted(set(filter(None, results))) if results.count(i) > 1]
    acs = {ac.id for ac in prd.acceptance_criteria}
    errors += [f"{row['id']} covers unknown AC {a}" for row in rows for a in row["ac_ids"] if a not in acs]
    errors += [f"{ac} is not covered by any check" for ac in sorted(acs - {a for r in rows for a in r["ac_ids"]})]
    errors += [f"{t.id} is not finished (agent status {tasks.get(t.id, {}).get('status')})"
               for t in plan.tasks if tasks.get(t.id, {}).get("status") != "done"]
    if not rows:
        errors.append("no check is planned")
    return errors


def phase_step(record, phase):
    return next((s for s in record.get("steps") or [] if s.get("phase") == phase), None)


def decide(record, rows, errors):
    """(final_status, reasons, cause_ambiguous) by the priority in FINAL_STATUSES."""
    if record is None:
        return "insufficient_evidence", ["verify.json이 없거나 읽을 수 없습니다."], False
    status = record.get("status")
    steps = record.get("steps") or []
    if status in NO_APP_EVIDENCE:
        return "insufficient_evidence", [NO_APP_EVIDENCE[status], *(record.get("failures") or [])], False
    if status not in KNOWN_STATUSES:
        return "insufficient_evidence", [f"알 수 없는 검증 상태입니다: {status}"], False
    docker = [s["phase"] for s in steps if s.get("outcome") == "docker_error"]
    if docker:
        return "insufficient_evidence", [f"Docker 오류로 {', '.join(docker)} 단계를 판단할 수 없습니다."], False
    if errors:
        return "insufficient_evidence", errors, False
    build = phase_step(record, "build")
    if build is None:
        return "insufficient_evidence", ["build 단계 기록이 없습니다."], False
    if status == "build_failed" or build.get("outcome") != "ok":
        ambiguous = build.get("outcome") in AMBIGUOUS_OUTCOMES
        return "build_failed", [f"build: {build.get('outcome')} (exit {build.get('exit_code')})"], ambiguous
    if (record.get("test") or {}).get("status") == "failed":
        return "test_failed", ["생성 앱의 테스트가 실패했습니다."], False

    restart = phase_step(record, "restart")
    stage_failures = []
    if record.get("root_status") != 200:
        stage_failures.append(f"앱이 응답하지 않습니다: GET / -> {record.get('root_status')}")
    if restart is None or restart.get("outcome") != "ok":
        stage_failures.append(f"재시작 실패: {(restart or {}).get('outcome')}")
    elif record.get("root_status_after_restart") != 200:
        stage_failures.append(f"재시작 후 응답 없음: GET / -> {record.get('root_status_after_restart')}")
    ran = [(r["id"], r[k]) for r in rows for k in ("first_run", "after_restart")]
    failed = sorted({i for i, state in ran if state == "failed"})
    if stage_failures or failed:
        ambiguous = bool(restart) and restart.get("outcome") in AMBIGUOUS_OUTCOMES
        return "auto_failed", stage_failures + [f"{i} 실패" for i in failed], ambiguous
    # Both starts answered, so a check without a result was skipped for no recorded reason.
    not_run = sorted({i for i, state in ran if state == "not_run"})
    if not_run:
        return "insufficient_evidence", [f"{i} 결과가 없습니다." for i in not_run], False
    if status == "checks_failed":
        return "insufficient_evidence", ["실패로 기록됐지만 실패한 점검이 없어 기록이 맞지 않습니다."], False

    manual = [(r["id"], m) for r in rows for m in r["manual"]]
    failed_manual = [f"{i} 수동 단계 {m['step_index']} 실패" for i, m in manual if m["state"] == "failed"]
    if failed_manual:
        return "manual_failed", failed_manual, False
    pending = [f"{i} 수동 단계 {m['step_index']} {'재확인 필요(다른 코드의 기록)' if m['state'] == 'stale' else '미기록'}"
               for i, m in manual if m["state"] in ("pending", "stale")]
    if pending:
        return "manual_pending", pending, False
    return "passed", ["계획된 자동 점검과 수동 확인이 모두 통과했습니다."], False


def gates(record, rows, final_status):
    record = record or {}
    build = phase_step(record, "build") or {}
    automatic = [r[k] for r in rows for k in ("first_run", "after_restart") if r[k] != "not_applicable"]
    manual = [m["state"] for r in rows for m in r["manual"]]
    return {
        "build": {"status": "not_run" if not build else "passed" if build.get("outcome") == "ok" else "failed",
                  "step_outcome": build.get("outcome")},
        # Not in the stack contract today, so a generated app has no test step; never counted as a pass.
        "test": {"status": (record.get("test") or {}).get("status", "not_applicable"),
                 "reason": (record.get("test") or {}).get("reason", "stack contract requires no tests"),
                 "counted_as_pass": False},
        "automatic": {"status": "failed" if final_status == "auto_failed" or "failed" in automatic else "not_applicable" if not automatic
                      else "not_run" if "not_run" in automatic else "passed",
                      "passed": automatic.count("passed"), "total": len(automatic)},
        "manual": {"status": "not_applicable" if not manual else "failed" if "failed" in manual
                   else "pending" if {"pending", "stale"} & set(manual) else "passed",
                   "planned_steps": len(manual), "passed": manual.count("passed"), "failed": manual.count("failed"),
                   "pending": manual.count("pending"), "stale": manual.count("stale")},
    }


def failure_targets(record, rows, final_status):
    """What failed, with where to read the evidence. The handoff to replanning and the required
    coverage of a diagnosis both come from this list."""
    record = record or {}
    targets = []
    steps = record.get("steps") or []
    for index, step in enumerate(steps):
        if step.get("phase") in ("build", "restart", "start") and step.get("outcome") != "ok":
            targets.append({"kind": step["phase"], "check_id": None, "ac_ids": [], "task_ids": [],
                            "detail": f"{step.get('outcome')} (exit {step.get('exit_code')})",
                            "evidence_ref": f"verify.json#/steps/{index}"})
    if final_status == "auto_failed":
        restart_failed = any(t["kind"] == "restart" for t in targets)
        for key, kind in (("root_status", "serve"), ("root_status_after_restart", "serve_after_restart")):
            if record.get(key) != 200 and not (kind == "serve_after_restart" and restart_failed):
                targets.append({"kind": kind, "check_id": None, "ac_ids": [], "task_ids": [],
                                "detail": f"GET / -> {record.get(key)}", "evidence_ref": f"verify.json#/{key}"})
    for row in rows:
        for run, name in (("first_run", "checks"), ("after_restart", "checks_after_restart")):
            if row[run] == "failed":
                results = record.get(name) or []
                index = next(i for i, c in enumerate(results) if c.get("id") == row["id"])
                bad = [{k: s.get(k) for k in ("method", "path", "status", "expect_status", "missing")}
                       for s in results[index].get("steps") or [] if not s.get("passed")]
                targets.append({"kind": "auto_check", "check_id": row["id"], "ac_ids": row["ac_ids"],
                                "task_ids": row["task_ids"], "detail": {"run": run, "failed_steps": bad},
                                "evidence_ref": f"verify.json#/{name}/{index}"})
        for step in row["manual"]:
            if step["state"] == "failed":
                targets.append({"kind": "manual_step", "check_id": row["id"], "ac_ids": row["ac_ids"],
                                "task_ids": row["task_ids"],
                                "detail": {"step_index": step["step_index"], "step": step["step"],
                                           "evidence": step["evidence"]},
                                "evidence_ref": "manual-review.json"})
    return targets


def build_verification(prd, spec, plan, record, raw_review, tasks, spec_sha, judged_at):
    """The whole verdict. `record` is verify.json or None; `tasks` is run.json's task records."""
    tasks = tasks or {}
    code_commit = (record or {}).get("code_commit")
    evidence_spec = (record or {}).get("spec_sha256")
    review = read_review(raw_review, spec, code_commit, spec_sha)
    rows = check_rows(spec, plan, record, review)
    acceptance, per_task = rollup(prd, plan, rows, tasks)
    errors = consistency_errors(prd, spec, plan, record or {}, rows, tasks)
    if evidence_spec and evidence_spec != spec_sha:
        errors.append("verify.json was made from a different Spec")
    final_status, reasons, ambiguous = decide(record, rows, errors)
    reasons = reasons + [f"수동 기록을 사용할 수 없습니다: {e}" for e in review["errors"]]
    targets = failure_targets(record, rows, final_status)
    return {
        "schema_version": 1, "judged_at": judged_at, "final_status": final_status, "reasons": reasons,
        "cause_ambiguous": ambiguous,
        # The automatic checks come from the model that wrote the Spec: not an independent test.
        "check_authorship": "spec_model",
        "basis": {"code_commit": code_commit, "spec_sha256": spec_sha,
                  "verify_status": (record or {}).get("status"), "failure_stage": (record or {}).get("failure_stage"),
                  "verify_failures": (record or {}).get("failures") or [],
                  "manual_review_state": review["state"]},
        "gates": gates(record, rows, final_status), "checks": rows, "acceptance": acceptance, "tasks": per_task,
        "evidence_refs": {"verify": "verify.json", "server_log": "verify.json#/server_log_tail",
                          "manual_review": "manual-review.json" if raw_review is not None else None},
        "diagnosis": dict(NO_DIAGNOSIS),
        "handoff": {"final_status": final_status, "route": ROUTES[final_status], "cause_ambiguous": ambiguous,
                    "reasons": reasons, "failures": targets, "suspected_task_ids": [], "replan_scope": None},
    }

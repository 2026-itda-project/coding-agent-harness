"""Offline tests for the #10 verdict. No model or Docker calls: the verify.json records are written here."""

import unittest

from app.m2 import acceptance
from app.m2.contracts import PRD, Plan, Spec

COMMIT, OTHER = "c" * 40, "d" * 40
DONE_TASK = {"title": "t", "status": "done", "attempt": 1, "commit": COMMIT, "agent": {"total_cost_usd": 0.1}}
SPEC_SHA = "s" * 64
HTTP = {"method": "GET", "path": "/api/x", "role_header": "", "body_json": "", "expect_status": 200, "expect_contains": []}


def item(id_):
    return {"id": id_, "description": "설명", "status": "confirmed", "source": "initial_input"}


def prd_data(acs):
    return {"title": "데모", "overview": {"summary": "s", "problem": "p", "goals": ["g"]},
            "in_scope": [], "out_of_scope": [], "roles": [item("ROLE-001")], "user_flows": [item("US-001")],
            "functional_requirements": [item("FR-001")], "business_rules": [], "nfrs": [],
            "acceptance_criteria": [{"id": a, "description": "d", "target_ids": ["FR-001"]} for a in acs],
            "open_issues": []}


def check(id_, acs, http=True, manual=(), rerun=False):
    return {"id": id_, "ac_ids": list(acs), "description": "d", "http_steps": [HTTP] if http else [],
            "rerun_after_restart": rerun, "manual_steps": list(manual)}


def spec_data(checks):
    return {"screens": [], "data_models": [], "apis": [], "permissions": [], "rule_implementations": [],
            "mock_integrations": [], "checks": checks}


def plan_data(checks):
    return {"features": [{"id": "FEAT-001", "name": "f", "spec_ids": []}],
            "tasks": [{"id": "TASK-001", "feature_id": "FEAT-001", "title": "t", "instructions": "do", "spec_ids": [],
                       "depends_on": [], "files": [], "acceptance": [], "check_ids": [c["id"] for c in checks]}]}


AUTO = check("CHK-001", ["AC-001"], rerun=True)
MANUAL = check("CHK-002", ["AC-001"], http=False, manual=["화면을 본다", "버튼을 누른다"])
MIXED = check("CHK-003", ["AC-001"], manual=["화면을 본다"])


def evidence(planned, **changes):
    """verify.json of a run in which every planned check ran and passed."""
    autos = [c for c in planned if c["http_steps"]]
    record = {"status": "manual_pending" if any(c["manual_steps"] for c in planned) else "passed",
              "automatic_status": "passed" if autos else "not_applicable", "failure_stage": None, "failures": [],
              "code_commit": COMMIT, "spec_sha256": SPEC_SHA,
              "steps": [{"phase": "build", "outcome": "ok", "exit_code": 0}, {"phase": "start", "outcome": "ok", "exit_code": 0},
                        {"phase": "restart", "outcome": "ok", "exit_code": 0}],
              "root_status": 200, "root_status_after_restart": 200,
              "checks": [{"id": c["id"], "passed": True, "steps": []} for c in autos],
              "checks_after_restart": [{"id": c["id"], "passed": True, "steps": []} for c in autos if c["rerun_after_restart"]]}
    return {**record, **changes}


def set_step(record, phase, outcome, code=1):
    return [{"phase": s["phase"], "outcome": outcome, "exit_code": code} if s["phase"] == phase else s
            for s in record["steps"]]


def stored(check_id, index, step, verdict="pass", commit=COMMIT, evidence_text="확인함"):
    return {"check_id": check_id, "step_index": index, "step": step, "verdict": verdict, "evidence": evidence_text,
            "reviewer": "검토자", "reviewed_at": "2026-10-31T00:00:00+00:00", "code_commit": commit, "spec_sha256": SPEC_SHA}


def review_of(*items):
    return {"schema_version": 1, "reviewer": "검토자", "reviewed_at": "2026-10-31T00:00:00+00:00", "items": list(items)}


def judge(checks, record, review=None, acs=("AC-001",), tasks=None):
    tasks = {"TASK-001": {"status": "done", "commit": COMMIT, "attempt": 1}} if tasks is None else tasks
    return acceptance.build_verification(PRD.model_validate(prd_data(acs)), Spec.model_validate(spec_data(checks)),
                                         Plan.model_validate(plan_data(checks)), record, review, tasks, SPEC_SHA, "now")


class FinalStatusTest(unittest.TestCase):
    def test_build_failure(self):
        record = evidence([AUTO])
        record = {**record, "status": "build_failed", "checks": [], "checks_after_restart": [],
                  "steps": set_step(record, "build", "failed")}
        result = judge([AUTO], record)
        self.assertEqual((result["final_status"], result["cause_ambiguous"], result["handoff"]["route"]),
                         ("build_failed", False, "replan_candidate"))
        self.assertEqual(result["gates"]["build"]["status"], "failed")
        self.assertEqual(result["handoff"]["failures"][0]["evidence_ref"], "verify.json#/steps/0")

    def test_build_timeout_and_kill_are_build_failed_with_an_ambiguous_cause(self):
        for outcome in ("timeout", "killed"):
            with self.subTest(outcome=outcome):
                record = evidence([AUTO])
                record = {**record, "status": "build_failed", "steps": set_step(record, "build", outcome)}
                result = judge([AUTO], record)
                self.assertEqual((result["final_status"], result["cause_ambiguous"]), ("build_failed", True))

    def test_docker_error_while_building_is_not_an_app_failure(self):
        record = evidence([AUTO])
        record = {**record, "status": "build_failed", "steps": set_step(record, "build", "docker_error", 125)}
        result = judge([AUTO], record)
        self.assertEqual((result["final_status"], result["handoff"]["route"]), ("insufficient_evidence", "environment"))

    def test_failed_automatic_check(self):
        record = evidence([AUTO], status="checks_failed", failure_stage="checks", checks=[
            {"id": "CHK-001", "passed": False, "steps": [
                {"method": "GET", "path": "/api/x", "status": 500, "expect_status": 200, "missing": [], "passed": False}]}])
        result = judge([AUTO], record)
        self.assertEqual(result["final_status"], "auto_failed")
        self.assertEqual(result["acceptance"][0]["state"], "auto_failed")
        target = result["handoff"]["failures"][0]
        self.assertEqual((target["check_id"], target["task_ids"], target["detail"]["failed_steps"][0]["status"]),
                         ("CHK-001", ["TASK-001"], 500))

    def test_stopped_verifications_and_missing_evidence_are_neither_pass_nor_app_failure(self):
        for status in ("environment_error", "workspace_error", "start_failed"):
            with self.subTest(status=status):
                # verify.stopped() records these as automatic_status failed, 0/N
                record = evidence([AUTO], status=status, automatic_status="failed", checks=[], checks_after_restart=[],
                                  failures=["stopped", "2 planned checks did not run"])
                result = judge([AUTO], record)
                self.assertEqual((result["final_status"], result["handoff"]["route"]),
                                 ("insufficient_evidence", "environment"))
        result = judge([AUTO], None)
        self.assertEqual((result["final_status"], result["acceptance"][0]["state"]), ("insufficient_evidence", "not_run"))

    def test_docker_error_on_restart_outranks_a_failed_check_and_keeps_it(self):
        record = evidence([AUTO], status="checks_failed", checks=[{"id": "CHK-001", "passed": False, "steps": []}],
                          checks_after_restart=[], root_status_after_restart=None)
        record["steps"] = set_step(record, "restart", "docker_error", 125)
        result = judge([AUTO], record)
        self.assertEqual(result["final_status"], "insufficient_evidence")
        self.assertEqual(result["checks"][0]["first_run"], "failed")
        self.assertIn("auto_check", [t["kind"] for t in result["handoff"]["failures"]])

    def test_app_that_does_not_answer_fails_and_its_checks_did_not_run(self):
        cases = [{"root_status": 500, "checks": [], "checks_after_restart": [], "root_status_after_restart": 500},
                 {"root_status_after_restart": None, "checks_after_restart": []}]
        for changes in cases:
            with self.subTest(changes=changes):
                result = judge([AUTO], evidence([AUTO], status="checks_failed", **changes))
                self.assertEqual(result["final_status"], "auto_failed")
                self.assertEqual(result["checks"][0]["after_restart"], "not_run")
                self.assertEqual(result["acceptance"][0]["state"], "not_run")

    def test_automatic_only_pass(self):
        result = judge([AUTO], evidence([AUTO]))
        self.assertEqual((result["final_status"], result["handoff"]["route"]), ("passed", "none"))
        self.assertEqual(result["acceptance"][0]["state"], "auto_passed")
        self.assertEqual(result["gates"]["manual"]["status"], "not_applicable")
        self.assertEqual(result["check_authorship"], "spec_model")

    def test_manual_only(self):
        record = evidence([MANUAL])
        pending = judge([MANUAL], record)
        self.assertEqual((pending["final_status"], pending["gates"]["automatic"]["status"], pending["handoff"]["route"]),
                         ("manual_pending", "not_applicable", "wait_for_human"))
        done = judge([MANUAL], record, review_of(stored("CHK-002", 0, "화면을 본다"), stored("CHK-002", 1, "버튼을 누른다")))
        self.assertEqual((done["final_status"], done["acceptance"][0]["state"]), ("passed", "manual_passed"))
        self.assertEqual(done["gates"]["automatic"], {"status": "not_applicable", "passed": 0, "total": 0})

    def test_mixed_checks(self):
        record = evidence([MIXED])
        self.assertEqual(judge([MIXED], record)["final_status"], "manual_pending")
        passed = judge([MIXED], record, review_of(stored("CHK-003", 0, "화면을 본다")))
        self.assertEqual(passed["final_status"], "passed")
        failed = judge([MIXED], record, review_of(stored("CHK-003", 0, "화면을 본다", "fail")))
        self.assertEqual((failed["final_status"], failed["handoff"]["route"]), ("manual_failed", "replan_candidate"))
        self.assertEqual(failed["handoff"]["failures"][0]["kind"], "manual_step")

    def test_partly_recorded_manual_steps_stay_pending(self):
        result = judge([MANUAL], evidence([MANUAL]), review_of(stored("CHK-002", 0, "화면을 본다")))
        self.assertEqual(result["final_status"], "manual_pending")
        self.assertEqual((result["gates"]["manual"]["passed"], result["gates"]["manual"]["pending"]), (1, 1))

    def test_test_gate_is_not_applicable_and_never_a_pass(self):
        gate = judge([AUTO], evidence([AUTO]))["gates"]["test"]
        self.assertEqual((gate["status"], gate["counted_as_pass"]), ("not_applicable", False))

    def test_a_failed_test_record_is_test_failed(self):
        # The stack contract has no test step, so verify never writes this record today.
        result = judge([AUTO], evidence([AUTO], test={"status": "failed", "reason": "x"}))
        self.assertEqual(result["final_status"], "test_failed")

    def test_priority_when_several_failures_apply(self):
        record = evidence([AUTO, MIXED], status="checks_failed", checks=[{"id": "CHK-001", "passed": False, "steps": []},
                                                                         {"id": "CHK-003", "passed": True, "steps": []}])
        result = judge([AUTO, MIXED], record, review_of(stored("CHK-003", 0, "화면을 본다", "fail")))
        self.assertEqual(result["final_status"], "auto_failed")
        self.assertEqual({t["kind"] for t in result["handoff"]["failures"]}, {"auto_check", "manual_step"})

    def test_evidence_that_disagrees_with_the_plan_supports_nothing(self):
        cases = {"unknown result": evidence([AUTO], checks=[{"id": "CHK-001", "passed": True}, {"id": "CHK-999", "passed": True}]),
                 "duplicate result": evidence([AUTO], checks=[{"id": "CHK-001", "passed": True}] * 2),
                 "missing result": evidence([AUTO], checks=[]),
                 "no build record": evidence([AUTO], steps=[]),
                 "other spec": evidence([AUTO], spec_sha256="x"),
                 "unknown status": evidence([AUTO], status="done"),
                 "failed without a failure": evidence([AUTO], status="checks_failed")}
        for name, record in cases.items():
            with self.subTest(name):
                self.assertEqual(judge([AUTO], record)["final_status"], "insufficient_evidence")
        self.assertEqual(judge([], evidence([]))["final_status"], "insufficient_evidence")
        unfinished = judge([AUTO], evidence([AUTO]), tasks={"TASK-001": {"status": "failed"}})
        self.assertEqual(unfinished["final_status"], "insufficient_evidence")

    def test_ac_takes_the_worst_state_of_its_checks(self):
        cases = [(("passed", "pending"), "manual_pending"), (("failed", "pass"), "auto_failed"),
                 (("passed", "pass"), "manual_passed"), (("passed", "fail"), "manual_failed")]
        for (auto, verdict), expected in cases:
            with self.subTest(auto=auto, verdict=verdict):
                record = evidence([MIXED], checks=[{"id": "CHK-003", "passed": auto == "passed", "steps": []}])
                review = None if verdict == "pending" else review_of(stored("CHK-003", 0, "화면을 본다", verdict))
                self.assertEqual(judge([MIXED], record, review)["acceptance"][0]["state"], expected)
        uncovered = judge([AUTO], evidence([AUTO]), acs=("AC-001", "AC-002"))
        self.assertEqual(([a["state"] for a in uncovered["acceptance"]], uncovered["final_status"]),
                         (["auto_passed", "no_evidence"], "insufficient_evidence"))

    def test_task_verdicts(self):
        idle = plan_data([AUTO])
        idle["tasks"].append({**idle["tasks"][0], "id": "TASK-002", "check_ids": []})
        result = acceptance.build_verification(
            PRD.model_validate(prd_data(["AC-001"])), Spec.model_validate(spec_data([AUTO])), Plan.model_validate(idle),
            evidence([AUTO], status="checks_failed", checks=[{"id": "CHK-001", "passed": False, "steps": []}]), None,
            {"TASK-001": {"status": "done", "attempt": 2}, "TASK-002": {"status": "done"}}, SPEC_SHA, "now")
        self.assertEqual([(t["id"], t["state"], t["diff"]) for t in result["tasks"]],
                         [("TASK-001", "auto_failed", "diffs/TASK-001-attempt-2.diff"),
                          ("TASK-002", "no_checks", "diffs/TASK-002-attempt-1.diff")])

    def test_stored_review_is_validated_when_judging(self):
        cases = {"old free form": {"ok": True}, "damaged": {"unreadable": True},
                 "no evidence": review_of(stored("CHK-003", 0, "화면을 본다", evidence_text="  ")),
                 "unknown check": review_of(stored("CHK-999", 0, "화면을 본다")),
                 "other text": review_of(stored("CHK-003", 0, "다른 문구"))}
        for name, review in cases.items():
            with self.subTest(name):
                result = judge([MIXED], evidence([MIXED]), review)
                self.assertEqual((result["final_status"], result["basis"]["manual_review_state"]),
                                 ("manual_pending", "invalid"))

    def test_review_of_other_code_does_not_count(self):
        result = judge([MIXED], evidence([MIXED]), review_of(stored("CHK-003", 0, "화면을 본다", commit=OTHER)))
        self.assertEqual((result["final_status"], result["gates"]["manual"]["stale"]), ("manual_pending", 1))
        # Evidence with no commit (a run from before #10) cannot be matched to any review.
        legacy = {k: v for k, v in evidence([MIXED]).items() if k != "code_commit"}
        self.assertEqual(judge([MIXED], legacy, review_of(stored("CHK-003", 0, "화면을 본다")))["final_status"], "manual_pending")

    def test_diagnosis_is_a_placeholder_and_the_handoff_has_its_fields(self):
        result = judge([AUTO], evidence([AUTO]))
        self.assertEqual(result["diagnosis"]["status"], "not_implemented")
        self.assertEqual(set(result["handoff"]), {"final_status", "route", "cause_ambiguous", "reasons", "failures",
                                                  "suspected_task_ids", "replan_scope"})


if __name__ == "__main__":
    unittest.main()

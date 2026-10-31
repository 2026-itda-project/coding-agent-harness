"""Offline tests for the #10 verdict. No model or Docker calls: the verify.json records are written here."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.llm import claude
from app.m2 import acceptance, agent, run, verify, web
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


class RunFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "run"
        self.art = self.run_dir / "artifacts"
        no_paid_calls = patch.object(claude, "complete", side_effect=AssertionError("model call"))
        no_docker = patch.object(verify, "run_group", side_effect=AssertionError("docker call"))
        for patcher in (no_paid_calls, no_docker):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, planned, **changes):
        run.dump(self.art / "prd.json", prd_data(["AC-001"]))
        run.dump(self.art / "spec.json", spec_data(planned))
        run.dump(self.art / "plan.json", plan_data(planned))
        sha = run.sha((self.art / "spec.json").read_text())
        run.dump(self.art / "verify.json", {**evidence(planned, spec_sha256=sha), **changes})
        run.dump(self.run_dir / "run.json", {"run_id": "t", "stages": {"code": "done"}, "calls": [], "attempts": [],
                                             "tasks": {"TASK-001": DONE_TASK}})

    def entry(self, check_id="CHK-003", index=0, step="화면을 본다", verdict="pass", evidence_text="목록이 보였다"):
        return {"check_id": check_id, "step_index": index, "step": step, "verdict": verdict, "evidence": evidence_text}

    def submit(self, *items, reviewer="검토자"):
        return run.submit_manual_review(self.run_dir, {"reviewer": reviewer, "items": list(items)})


class ManualReviewTest(RunFiles):
    def test_accepted_review_is_stamped_and_changes_the_verdict(self):
        self.write([MIXED])
        result = self.submit(self.entry())
        self.assertEqual(result["final_status"], "passed")
        stored_item = run.load(self.art / "manual-review.json")["items"][0]
        self.assertEqual((stored_item["reviewer"], stored_item["code_commit"]), ("검토자", COMMIT))
        self.assertTrue(stored_item["reviewed_at"])
        self.assertEqual(run.load(self.art / "verification.json")["final_status"], "passed")
        self.assertEqual(run.load(self.run_dir / "run.json")["final_status"], "passed")

    def test_refused_input_is_kept_and_changes_nothing(self):
        self.write([MIXED])
        self.submit(self.entry(verdict="fail"))
        before = (self.art / "manual-review.json").read_text()
        cases = {"no evidence": [self.entry(evidence_text=" ")], "unknown check": [self.entry(check_id="CHK-999")],
                 "out of range": [self.entry(index=5)], "other text": [self.entry(step="다른 문구")],
                 "duplicate": [self.entry(), self.entry()], "empty": [],
                 "bad verdict": [self.entry(verdict="maybe")]}
        for name, items in cases.items():
            with self.subTest(name), self.assertRaises(run.ManualReviewRejected):
                self.submit(*items)
            saved = run.load(self.art / "manual-review.rejected.json")
            self.assertEqual(saved["data"]["items"], items)
            self.assertTrue(saved["errors"])
        with self.assertRaises(run.ManualReviewRejected):
            self.submit(self.entry(), reviewer=" ")
        with self.assertRaises(run.ManualReviewRejected):  # the server records the time, not the client
            run.submit_manual_review(self.run_dir, {"reviewer": "r", "reviewed_at": "2000", "items": [self.entry()]})
        self.assertEqual((self.art / "manual-review.json").read_text(), before)

    def test_partial_submissions_merge_and_history_keeps_what_they_replaced(self):
        self.write([MANUAL])
        first = self.submit(self.entry("CHK-002", 0, "화면을 본다", "fail"))
        self.assertEqual(first["final_status"], "manual_failed")
        second = self.submit(self.entry("CHK-002", 1, "버튼을 누른다"))
        self.assertEqual(second["final_status"], "manual_failed")
        third = self.submit(self.entry("CHK-002", 0, "화면을 본다"), reviewer="다른 검토자")
        self.assertEqual(third["final_status"], "passed")
        lines = [json.loads(l) for l in (self.art / "manual-review.history.jsonl").read_text().splitlines()]
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[2]["replaced"][0]["verdict"], "fail")
        self.assertEqual(run.load(self.art / "manual-review.json")["reviewer"], "다른 검토자")

    def test_only_a_verification_with_a_running_app_can_be_reviewed(self):
        for status in ("environment_error", "build_failed", "start_failed", "passed"):
            with self.subTest(status=status):
                self.write([MIXED], status=status)
                with self.assertRaises(run.ManualReviewRejected):
                    self.submit(self.entry())
        with self.assertRaises(run.ManualReviewRejected):
            self.write([MIXED], code_commit=None)
            self.submit(self.entry())

    def test_a_stored_file_that_fails_the_contract_is_not_overwritten(self):
        self.write([MIXED])
        (self.art / "manual-review.json").write_text("{not json")
        with self.assertRaises(run.ManualReviewRejected):
            self.submit(self.entry())
        self.assertEqual((self.art / "manual-review.json").read_text(), "{not json")
        self.assertEqual(run.judge_run(self.run_dir, {})["basis"]["manual_review_state"], "invalid")


class JudgeCommandTest(RunFiles):
    def test_judge_records_the_verdict_without_docker_or_a_model(self):
        self.write([MIXED])
        run.main(["judge", "--run-dir", str(self.run_dir)])
        self.assertEqual(run.load(self.art / "verification.json")["final_status"], "manual_pending")
        state = run.load(self.run_dir / "run.json")
        self.assertEqual((state["final_status"], state["run_id"]), ("manual_pending", "t"))

    def test_manual_review_command(self):
        self.write([MIXED])
        path = Path(self.tmp.name) / "review.json"
        path.write_text(json.dumps({"reviewer": "r", "items": [self.entry()]}))
        run.main(["manual-review", "--run-dir", str(self.run_dir), "--input", str(path)])
        self.assertEqual(run.load(self.art / "verification.json")["final_status"], "passed")
        path.write_text(json.dumps({"reviewer": "r", "items": [self.entry(evidence_text="")]}))
        with self.assertRaises(SystemExit):
            run.main(["manual-review", "--run-dir", str(self.run_dir), "--input", str(path)])

    def test_commands_refuse_a_run_that_is_still_running(self):
        self.write([MIXED])
        run.dump(self.run_dir / "web.json", {"status": "running"})
        with self.assertRaises(SystemExit):
            run.main(["judge", "--run-dir", str(self.run_dir)])
        self.assertFalse((self.art / "verification.json").exists())

    def test_judging_a_run_without_evidence(self):
        self.write([AUTO])
        (self.art / "verify.json").unlink()
        run.main(["judge", "--run-dir", str(self.run_dir)])
        self.assertEqual(run.load(self.art / "verification.json")["final_status"], "insufficient_evidence")
        (self.art / "verify.json").write_text("{damaged")
        run.main(["judge", "--run-dir", str(self.run_dir)])
        self.assertEqual(run.load(self.art / "verification.json")["final_status"], "insufficient_evidence")


class RunnerConnectionTest(RunFiles):
    def prepare(self, checks):
        env = patch.dict(os.environ, {"SONNET_MODEL": "claude-sonnet-5-5", "OPUS_MODEL": "claude-opus-5-5"})
        env.start()
        self.addCleanup(env.stop)
        self.app = agent.init_workspace(self.run_dir / "app")
        self.write(checks)
        run.dump(self.run_dir / "run.json", {"run_id": "t", "stages": {}, "calls": [], "attempts": [], "task_order": [],
                                             "tasks": {"TASK-001": DONE_TASK}})
        self.resume = ["run", "--resume", str(self.run_dir)]

    def test_verification_is_written_and_tied_to_the_code_it_checked(self):
        self.prepare([AUTO])
        head = agent.git(self.app, "rev-parse", "HEAD").strip()
        result = {k: v for k, v in evidence([AUTO]).items() if k not in ("code_commit", "spec_sha256")}
        with patch.object(verify, "verify", lambda *a, **k: result):
            run.main(self.resume)
        record = run.load(self.art / "verify.json")
        self.assertEqual((record["code_commit"], record["spec_sha256"]), (head, run.sha((self.art / "spec.json").read_text())))
        self.assertEqual(run.load(self.art / "verification.json")["final_status"], "passed")
        self.assertEqual(run.load(self.run_dir / "run.json")["final_status"], "passed")

    def test_interrupted_verification_leaves_no_old_verdict(self):
        self.prepare([AUTO])
        run.main(["judge", "--run-dir", str(self.run_dir)])
        self.assertEqual(run.load(self.art / "verification.json")["final_status"], "passed")
        with patch.object(verify, "verify", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            run.main(self.resume)
        self.assertFalse((self.art / "verification.json").exists())
        self.assertFalse((self.art / "verify.json").exists())
        self.assertEqual(run.load(self.art / "verify.previous.json")["status"], "passed")
        state = run.load(self.run_dir / "run.json")
        self.assertNotIn("final_status", state)
        self.assertEqual(run.judge_run(self.run_dir, state["tasks"])["final_status"], "insufficient_evidence")

    def test_verifying_the_same_code_again_keeps_manual_passes(self):
        self.prepare([MIXED])
        head = agent.git(self.app, "rev-parse", "HEAD").strip()
        make = lambda: {k: v for k, v in evidence([MIXED]).items() if k not in ("code_commit", "spec_sha256")}
        with patch.object(verify, "verify", lambda *a, **k: make()):
            run.main(self.resume)
        self.submit(self.entry())
        with patch.object(verify, "verify", lambda *a, **k: make()):
            run.main(self.resume)
        self.assertEqual(run.load(self.art / "verification.json")["final_status"], "passed")
        agent.git(self.app, "commit", "-q", "--allow-empty", "-m", "changed code")
        self.assertNotEqual(head, agent.git(self.app, "rev-parse", "HEAD").strip())
        with patch.object(verify, "verify", lambda *a, **k: make()):
            run.main(self.resume)
        self.assertEqual(run.load(self.art / "verification.json")["final_status"], "manual_pending")


class WebStatusTest(RunFiles):
    def setUp(self):
        super().setUp()
        for patcher in (patch.object(run, "WORKSPACE_ROOT", Path(self.tmp.name)),
                        patch.object(web, "preview_running", lambda run_id: False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.prd_id = "0123456789abcdef"
        self.run_id = f"web-{self.prd_id}"
        self.run_dir = Path(self.tmp.name) / self.run_id
        self.art = self.run_dir / "artifacts"
        self.addCleanup(web._threads.pop, self.run_id, None)
        run.dump(self.run_dir / "web.json", {"status": "finished", "prd_id": self.prd_id, "started_at": "t"})

    def status(self):
        return web.run_status(self.run_id)

    def test_outcome_table(self):
        pass_mixed = review_of(stored("CHK-003", 0, "화면을 본다"))
        fail_mixed = review_of(stored("CHK-003", 0, "화면을 본다", "fail"))
        pass_manual = review_of(stored("CHK-002", 0, "화면을 본다"), stored("CHK-002", 1, "버튼을 누른다"))
        cases = [([AUTO], {}, None, "passed", "auto_checks_passed", "not_required"),
                 ([MIXED], {}, pass_mixed, "passed", "auto_checks_passed", "done"),
                 ([MANUAL], {}, pass_manual, "passed", "manual_check_only", "done"),
                 ([MIXED], {}, None, "manual_pending", "auto_checks_passed", "pending"),
                 ([MANUAL], {}, None, "manual_pending", "manual_check_only", "pending"),
                 ([MIXED], {}, fail_mixed, "manual_failed", "verification_failed", "failed"),
                 ([MIXED], {}, {"old": True}, "manual_pending", "auto_checks_passed", "invalid"),
                 ([AUTO], {"status": "checks_failed", "checks": [{"id": "CHK-001", "passed": False}]}, None,
                  "auto_failed", "verification_failed", "not_required"),
                 ([AUTO], {"status": "environment_error"}, None, "insufficient_evidence", "verification_failed", "not_required")]
        for checks, changes, review, final, outcome, manual in cases:
            with self.subTest(final=final, outcome=outcome, manual=manual):
                self.write(checks, **changes)
                (self.art / "manual-review.json").unlink(missing_ok=True)
                if review:
                    sha = run.sha((self.art / "spec.json").read_text())
                    if "items" in review:
                        review = {**review, "items": [{**i, "spec_sha256": sha} for i in review["items"]]}
                    run.dump(self.art / "manual-review.json", review)
                status = self.status()
                self.assertEqual((status["final_status"], status["outcome"], status["manual_review"]), (final, outcome, manual))
                self.assertEqual(status["verification"]["final_status"], final)

    def test_reasons_tell_failures_apart(self):
        self.write([AUTO], status="start_failed")
        self.assertIn("Docker", self.status()["reason"])
        self.write([MIXED])
        run.submit_manual_review(self.run_dir, {"reviewer": "r", "items": [self.entry(verdict="fail")]})
        self.assertIn("사람 확인", self.status()["reason"])

    def test_a_record_with_no_spec_keeps_the_old_status(self):
        run.dump(self.art / "verify.json", {"status": "manual_pending", "automatic_status": "passed"})
        (self.art / "manual-review.json").write_text("{}")  # no longer "done" just because a file exists
        status = self.status()
        self.assertEqual((status["final_status"], status["outcome"], status["manual_review"]),
                         (None, "auto_checks_passed", "pending"))

    def test_no_verdict_while_running_or_before_verification(self):
        self.write([AUTO])
        run.dump(self.run_dir / "web.json", {"status": "running", "prd_id": self.prd_id, "started_at": "t"})
        with patch.object(web, "_threads", {self.run_id: type("Alive", (), {"is_alive": lambda s: True})()}):
            self.assertEqual(self.status()["final_status"], None)
        run.dump(self.run_dir / "web.json", {"status": "failed", "prd_id": self.prd_id, "started_at": "t", "error": "TASK-001 failed"})
        (self.art / "verify.json").unlink()
        run.dump(self.run_dir / "run.json", {"stages": {"code": "failed at TASK-001"}, "tasks": {}})
        self.assertEqual(self.status()["final_status"], None)

    def test_interrupted_during_verification_has_no_evidence(self):
        self.write([AUTO])
        run.dump(self.run_dir / "web.json", {"status": "interrupted", "prd_id": self.prd_id, "started_at": "t"})
        (self.art / "verify.json").unlink()
        status = self.status()
        self.assertEqual((status["outcome"], status["final_status"]), ("interrupted", "insufficient_evidence"))

    def test_damaged_verify_record_is_reported_not_raised(self):
        self.write([AUTO])
        (self.art / "verify.json").write_text("{damaged")
        status = self.status()
        self.assertEqual((status["final_status"], status["outcome"], status["verify"]),
                         ("insufficient_evidence", "verification_failed", None))

    def test_api_records_a_review_and_refuses_bad_input(self):
        from fastapi import HTTPException

        from app import main
        self.write([MIXED])
        status = main.m2_manual_review(self.run_id, {"reviewer": "r", "items": [self.entry()]})
        self.assertEqual((status["final_status"], status["manual_review"]), ("passed", "done"))
        with self.assertRaises(HTTPException) as refused:
            main.m2_manual_review(self.run_id, {"reviewer": "r", "items": [self.entry(evidence_text="")]})
        self.assertEqual(refused.exception.status_code, 422)
        self.assertTrue((self.art / "manual-review.rejected.json").exists())
        with patch.object(web, "_threads", {self.run_id: type("Alive", (), {"is_alive": lambda s: True})()}), \
                self.assertRaises(HTTPException) as busy:
            main.m2_manual_review(self.run_id, {"reviewer": "r", "items": [self.entry()]})
        self.assertEqual(busy.exception.status_code, 409)
        with self.assertRaises(HTTPException) as unknown:
            main.m2_manual_review("web-ffffffffffffffff", {"reviewer": "r", "items": []})
        self.assertEqual(unknown.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()

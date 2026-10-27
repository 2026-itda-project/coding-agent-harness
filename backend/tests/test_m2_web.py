"""Offline tests for the Review -> M2 connection. Claude, Claude Code and Docker are all mocked:
these prove wiring and contracts, not model quality or real code generation."""

import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from fastapi import HTTPException

from app import pipeline
from app.llm import claude
from app.llm.errors import AnalysisError
from app.m2 import run, stages, web
from app.m2.contracts import ReviewedPRD, check_reviewed_prd
from app import main
from app.schemas import (Answer, ClarificationQuestion, ClarificationRound, Gap, GapLink, Requirement,
                         WorkflowState)

PID = "project-x"
ENV = {"ANTHROPIC_API_KEY": "test-only", "SONNET_MODEL": "claude-sonnet-5-5", "OPUS_MODEL": "claude-opus-5-5"}


def req(id_, description, status="confirmed", source="initial_input", category="functional", ac=()):
    return Requirement(project_id=PID, id=id_, category=category, description=description, status=status,
                       source=source, blocking=False, acceptance_criteria=list(ac))


def gap(id_, related, description):
    return Gap(id=id_, related_requirement_ids=related, category="business_rule", description=description,
               blocking=False)


def approved_state():
    """A small meeting-room booking handoff (not the leave-management example) covering every
    status/source the PRD must keep apart."""
    reqs = [req("REQ-001", "직원과 관리자가 있다.", category="role"),
            req("REQ-002", "직원은 회의실을 30분 단위로 예약한다."),           # edited in Review (was 1시간)
            req("REQ-003", "예약 승인 없이 바로 확정한다.", source="clarification_answer"),
            req("REQ-004", "관리자는 예약을 취소할 수 있다.", status="confirmed", source="ai_proposal"),  # accepted
            req("REQ-005", "예약 10분 전에 알림을 보낸다.", status="proposed", source="ai_proposal"),   # not accepted
            req("REQ-006", "회의실별 최대 인원을 표시한다.", source="review_input"),
            req("REQ-007", "반복 예약 지원 여부", status="needs_clarification", category="scope")]
    history = [ClarificationRound(round=1, gaps=[], questions=[ClarificationQuestion(
        gap_id="GAP-009", question="예약에 승인이 필요한가요?", ai_proposal="승인 없이 확정")],
        answers=[Answer(gap_id="GAP-009", answer="승인 없이 바로 확정해 주세요")])]
    return WorkflowState(project_id=PID, run_id="analysis-1", requirements=reqs,
                         gaps=[gap("GAP-001", [], "예약 가능 시간대가 정해지지 않았다.")],
                         gap_links=[GapLink(gap=gap("GAP-002", ["REQ-006"], "인원 표시 방식"), requirement_id="REQ-006")],
                         clarification_needed=False, analysis_mode="claude", text="회의실 예약 서비스를 1시간 단위로 만들어줘",
                         phase="approved", history=history, clarification_round=1)


def item(id_, refs, status="confirmed", source="initial_input", gaps=()):
    return {"id": id_, "description": f"{id_} 설명", "status": status, "source": source,
            "requirement_ids": list(refs), "gap_ids": list(gaps)}


def prd_output(**changes):
    data = {"title": "회의실 예약", "overview": {"summary": "s", "problem": "p", "goals": ["g"]},
            "in_scope": [item("SCOPE-001", [], source="harness_policy")], "out_of_scope": [],
            "roles": [item("ROLE-001", ["REQ-001"])],
            "user_flows": [item("US-001", ["REQ-002"])],
            "functional_requirements": [
                item("FR-001", ["REQ-002"]),
                item("FR-002", ["REQ-003"], source="clarification_answer"),
                item("FR-003", ["REQ-004"], source="ai_proposal"),
                item("FR-004", ["REQ-005"], status="proposed", source="ai_proposal"),
                item("FR-005", ["REQ-006"], source="review_input", gaps=["GAP-002"]),
                item("FR-006", [], status="proposed", source="ai_proposal", gaps=["GAP-001"])],
            "business_rules": [], "nfrs": [],
            "acceptance_criteria": [{"id": f"AC-00{n}", "description": "된다", "target_ids": [f"FR-00{n}"]}
                                    for n in range(1, 7)],
            "open_issues": [item("ISSUE-001", ["REQ-007"], status="needs_clarification")]}
    data.update(changes)
    return data


def handoff():
    return web.approve(approved_state())[1]


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(body=None, error=None):
    class Opener:
        def open(self, request, timeout):
            Opener.request = json.loads(request.data)
            if error:
                raise error
            return FakeResponse(sse(body))
    return lambda *a: Opener()


def sse(message):
    """Encode a Messages API response as the server-sent events of a streamed reply."""
    events = [("message_start", {"message": {"model": message["model"],
                                             "usage": {"input_tokens": message["usage"]["input_tokens"]}}})]
    for i, block in enumerate(message["content"]):
        events.append(("content_block_start", {"index": i, "content_block": {"type": block["type"]}}))
        if block["type"] == "text":
            events.append(("content_block_delta", {"index": i, "delta": {"type": "text_delta", "text": block["text"]}}))
    events.append(("message_delta", {"delta": {"stop_reason": message["stop_reason"]},
                                     "usage": {"output_tokens": message["usage"]["output_tokens"]}}))
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def message(text, model="claude-sonnet-5-5"):
    return {"model": model, "stop_reason": "end_turn", "usage": {"input_tokens": 1000, "output_tokens": 100},
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}]}


class ClaudeProviderTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {**ENV, "LLM_PROVIDER": "claude"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def extract(self, opener):
        with patch.object(claude, "build_opener", opener):
            records = []
            try:
                return pipeline.extract_stage(pipeline.get_provider(), "회의실 예약", PID, "run-1", records), records
            finally:
                self.records = records

    def test_normal_response_records_model_tokens_and_cost(self):
        body = {"requirements": [{"project_id": PID, "id": "REQ-001", "category": "functional",
                                  "description": "회의실을 예약한다.", "status": "confirmed",
                                  "source": "initial_input", "blocking": False, "acceptance_criteria": []}]}
        opener = opener_returning(message(json.dumps(body)))
        reqs, records = self.extract(opener)
        self.assertEqual(reqs[0].id, "REQ-001")
        self.assertEqual(pipeline.get_provider().mode, "claude")
        sent = opener().request
        self.assertEqual(sent["model"], "claude-sonnet-5-5")
        self.assertEqual(sent["output_config"]["format"]["type"], "json_schema")
        self.assertEqual((records[0]["model"], records[0]["input_tokens"], records[0]["output_tokens"]),
                         ("claude-sonnet-5-5", 1000, 100))
        self.assertAlmostEqual(records[0]["cost"], 0.003)

    def test_errors_are_explicit_and_never_fall_back(self):
        cases = [
            (opener_returning(error=HTTPError("u", 401, "unauthorized", {}, io.BytesIO(b"{}"))), "authentication_failed"),
            (opener_returning(message("")), "empty_response"),
            (opener_returning(message('{"requirements": [{"id": 1}]}')), "structured_output_invalid"),
            (opener_returning(message("{}", model="claude-opus-5-5")), "model_unavailable"),
        ]
        for opener, code in cases:
            with self.subTest(code=code), self.assertRaises(AnalysisError) as ctx:
                self.extract(opener)
            self.assertEqual(ctx.exception.code, code)
            self.assertEqual(self.records[-1]["error"], code)
        self.assertIsNotNone(self.records[-1].get("cost"))  # a billed but invalid reply still records cost

    def test_interrupted_call_records_unknown_cost_and_blocks_budget(self):
        from urllib.error import URLError

        class Cut(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):  # the stream starts, then the connection drops
                yield b'event: message_start\n'
                yield b'data: {"message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 50000}}}\n'
                raise TimeoutError("read timed out")

        cases = [(lambda *a: type("O", (), {"open": lambda self, r, timeout: Cut()})(), True),
                 (opener_returning(error=URLError(TimeoutError("timed out"))), True),
                 (opener_returning(error=URLError(ConnectionRefusedError())), False)]
        for opener, billed in cases:
            log = []
            with self.subTest(billed=billed), patch.object(claude, "build_opener", opener), \
                    self.assertRaises(AnalysisError):
                claude.complete("claude-opus-5-5", "s", {}, {}, log)
            self.assertEqual(len(log), int(billed))
            if billed:
                self.assertIsNone(log[0]["cost_usd"])
                with self.assertRaises(SystemExit):  # unknown cost is never counted as 0
                    run.Budget(100, lambda: [r["cost_usd"] for r in log]).check(1, "next")

    def test_missing_configuration_is_an_error(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "claude"}, clear=True), self.assertRaises(AnalysisError) as ctx:
            pipeline.get_provider()
        self.assertEqual(ctx.exception.code, "missing_api_key")

    def test_mock_and_snowchat_selection_unchanged(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "mock"}):
            self.assertEqual(pipeline.get_provider().mode, "mock")
        with patch.dict(os.environ, {"LLM_PROVIDER": "nope"}), self.assertRaises(AnalysisError):
            pipeline.get_provider()


class ReviewedPRDTest(unittest.TestCase):
    def check(self, **changes):
        return check_reviewed_prd(ReviewedPRD.model_validate(prd_output(**changes)), handoff())

    def test_valid_prd_keeps_every_status_and_source(self):
        self.assertEqual(self.check(), [])

    def test_rejects_mislabelled_or_lost_content(self):
        data = prd_output()
        frs = data["functional_requirements"]
        frs[1]["source"] = "initial_input"                         # clarification answer flattened
        frs[3] = item("FR-004", ["REQ-005"], source="ai_proposal")  # unaccepted default shown as confirmed
        frs[4]["gap_ids"] = []                                     # linked gap history dropped
        data["open_issues"] = [item("ISSUE-001", [], status="needs_clarification", source="ai_proposal",
                                    gaps=["GAP-002"])]              # settled gap reopened, REQ-007 lost
        errors = " | ".join(check_reviewed_prd(ReviewedPRD.model_validate(data), handoff()))
        for part in ("FR-002: confirmed initial_input is not backed", "FR-004: confirmed ai_proposal is not backed",
                     "REQ-007 from the Review handoff is missing", "REQ-007 is undecided but not in open_issues",
                     "GAP-002 is linked to REQ-006 in Review"):
            self.assertIn(part, errors)

    def test_gap_linked_to_undecided_requirement_stays_open_with_it(self):
        state = approved_state()
        state.gap_links.append(GapLink(gap=gap("GAP-003", ["REQ-007"], "반복 주기"), requirement_id="REQ-007"))
        data = prd_output(open_issues=[item("ISSUE-001", ["REQ-007"], status="needs_clarification", gaps=["GAP-003"])])
        self.assertEqual(check_reviewed_prd(ReviewedPRD.model_validate(data), web.approve(state)[1]), [])

    def test_rejects_unknown_ids_and_missing_open_gap(self):
        data = prd_output()
        data["functional_requirements"][5] = item("FR-006", ["REQ-404"], status="proposed", source="ai_proposal",
                                                  gaps=["GAP-404"])
        errors = " | ".join(check_reviewed_prd(ReviewedPRD.model_validate(data), handoff()))
        self.assertIn("FR-006 references unknown requirement ids", errors)
        self.assertIn("GAP-001 (open gap) is missing", errors)

    def test_failed_billed_call_keeps_its_stage_and_opus_cap(self):
        def fake(model, system, payload, schema, log, **kw):
            log.append({"model": model, "cost_usd": 0.68, "max_tokens": kw["max_tokens"]})
            raise AnalysisError("incomplete_response", "max_tokens")

        log = []
        prd = ReviewedPRD.model_validate(prd_output())
        with patch.dict(os.environ, ENV), patch.object(claude, "complete", fake), self.assertRaises(AnalysisError):
            stages.generate_spec(prd, log)
        self.assertEqual((log[0]["stage"], log[0]["max_tokens"]), ("spec", stages.OPUS_MAX_TOKENS))

    def test_prompt_payload_uses_the_reviewed_state(self):
        captured = {}

        def fake(model, system, payload, schema, log, **kw):
            captured.update(payload=payload, system=system, model=model, constrained=kw.get("constrained"))
            log.append({"model": model, "cost_usd": 0.01})
            return prd_output()

        state = approved_state()
        with patch.dict(os.environ, ENV), patch.object(claude, "complete", fake):
            prd = stages.generate_reviewed_prd(state.text, state.history, handoff(), [])
        reviewed = {r["id"]: r for r in captured["payload"]["reviewed"]["requirements"]}
        self.assertEqual(reviewed["REQ-002"]["description"], "직원은 회의실을 30분 단위로 예약한다.")
        self.assertEqual(captured["payload"]["clarifications"],
                         [{"question": "예약에 승인이 필요한가요?", "answer": "승인 없이 바로 확정해 주세요"}])
        self.assertIn("review is the source of truth", captured["system"])
        self.assertEqual(captured["model"], "claude-sonnet-5-5")
        self.assertEqual(prd.functional_requirements[3].status, "proposed")


class Result:
    def __init__(self, status_code, body):
        self.status_code, self.body = status_code, body

    def json(self):
        return self.body


class Client:
    """Calls the FastAPI route functions directly (no HTTP client dependency), keeping their status codes."""
    routes = {("POST", "/api/m2/prd"): (main.m2_prd, main.M2PRDRequest),
              ("POST", "/api/m2/runs"): (main.m2_start_run, main.M2RunRequest)}

    def call(self, fn, *args):
        try:
            return Result(200, fn(*args))
        except HTTPException as exc:
            return Result(exc.status_code, exc.detail)

    def post(self, path, json):
        fn, model = self.routes[("POST", path)]
        return self.call(fn, model.model_validate(json))

    def get(self, path):
        return self.call(main.m2_run_status, path.rsplit("/", 1)[1])


class WebFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, value in ((run, "WORKSPACE_ROOT"), ):
            patcher = patch.object(target, value, Path(self.tmp.name))
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)
        self.calls = 0
        self.client = Client()

    def fake_complete(self, output):
        def fake(model, system, payload, schema, log, **kw):
            self.calls += 1
            log.append({"model": model, "usage": {"input_tokens": 10, "output_tokens": 10}, "cost_usd": 0.02})
            return output
        return patch.object(claude, "complete", fake)

    def make_prd(self, output=None):
        with self.fake_complete(output or prd_output()):
            return self.client.post("/api/m2/prd", json={"state": approved_state().model_dump()})

    def test_same_approved_state_reuses_the_prd(self):
        first = self.make_prd().json()
        second = self.make_prd().json()
        self.assertEqual((first["status"], first["prd_id"]), ("passed", second["prd_id"]))
        self.assertEqual(self.calls, 1)
        provenance = run.load(web.prd_dir(first["prd_id"]) / "provenance.json")
        self.assertEqual((provenance["analysis_run_id"], provenance["calls"][0]["cost_usd"]), ("analysis-1", 0.02))

    def test_unknown_cost_of_an_earlier_prd_attempt_blocks_the_next_call(self):
        def cut(model, system, payload, schema, log, **kw):
            self.calls += 1
            log.append({"model": model, "usage": {}, "cost_usd": None, "error": "interrupted: TimeoutError"})
            raise AnalysisError("gateway_error", "끊김")

        with patch.object(claude, "complete", cut):
            body = {"state": approved_state().model_dump()}
            self.assertEqual(self.client.post("/api/m2/prd", json=body).status_code, 502)
            second = self.client.post("/api/m2/prd", json=body)
        self.assertEqual((second.status_code, self.calls), (409, 1))
        self.assertIn("unknown cost", second.json()["message"])

    def test_unapproved_state_and_rejected_prd_cannot_run(self):
        state = approved_state().model_copy(update={"phase": "review"})
        self.assertEqual(self.client.post("/api/m2/prd", json={"state": state.model_dump()}).status_code, 422)
        rejected = self.make_prd(prd_output(open_issues=[])).json()
        self.assertEqual(rejected["status"], "rejected")
        started = self.client.post("/api/m2/runs", json={"prd_id": rejected["prd_id"]})
        self.assertEqual(started.status_code, 422)
        self.assertFalse((Path(self.tmp.name) / f"web-{rejected['prd_id']}").exists())

    def start(self, fake_main, confirm=True):
        prd_id = self.make_prd().json()["prd_id"]
        with patch.object(run, "main", fake_main):
            response = self.client.post("/api/m2/runs", json={"prd_id": prd_id, "confirm_open_issues": confirm})
            web._threads[response.json()["run_id"]].join(5) if response.status_code == 200 else None
        return prd_id, response

    def test_runs_the_exact_web_prd_once_and_reports_manual_pending(self):
        seen = []

        def fake_main(argv):
            seen.append(argv)
            run_id = argv[argv.index("--run-id") + 1]
            art = Path(self.tmp.name) / run_id / "artifacts"
            run.dump(art / "verify.json", {"status": "manual_pending", "automatic_status": "passed", "passed": 3, "total": 3, "failures": [],
                                           "preview_url": "http://127.0.0.1:5555"})
            run.dump(art.parent / "run.json", {"stages": {"spec": "done", "plan": "done", "code": "done",
                                                          "verify": "manual_pending"}, "tasks": {}})

        prd_id, response = self.start(fake_main)
        self.assertEqual(response.json(), {"run_id": f"web-{prd_id}", "created": True})
        prd_arg = seen[0][seen[0].index("--prd") + 1]
        self.assertEqual(Path(prd_arg), web.prd_dir(prd_id) / "prd.json")
        self.assertEqual(seen[0][seen[0].index("--budget-usd") + 1], str(web.RUN_BUDGET_USD))
        again = self.client.post("/api/m2/runs", json={"prd_id": prd_id, "confirm_open_issues": True})
        self.assertEqual(again.json()["created"], False)
        self.assertEqual(len(seen), 1)
        with patch.object(web, "preview_running", lambda run_id: True):
            status = self.client.get(f"/api/m2/runs/web-{prd_id}").json()
        self.assertEqual((status["outcome"], status["manual_review"], status["preview_url"]),
                         ("auto_checks_passed", "pending", "http://127.0.0.1:5555"))

    def test_open_issues_need_explicit_confirmation(self):
        _, response = self.start(lambda argv: None, confirm=False)
        self.assertEqual(response.status_code, 409)

    def test_failures_are_not_success(self):
        def stopped(argv):
            raise SystemExit("TASK-002 failed; see agent/TASK-002-attempt-1.json")

        prd_id, _ = self.start(stopped)
        status = self.client.get(f"/api/m2/runs/web-{prd_id}").json()
        self.assertEqual((status["outcome"], status["error"]), ("failed", "TASK-002 failed; see agent/TASK-002-attempt-1.json"))

    def test_retry_resumes_the_same_failed_run_only_on_request(self):
        calls = []

        def first(argv):
            calls.append(argv)
            run_id = argv[argv.index("--run-id") + 1]
            run.dump(Path(self.tmp.name) / run_id / "run.json", {"stages": {"error": "x"}, "calls": [
                {"stage": "spec", "cost_usd": 0.68}], "tasks": {}, "attempts": []})
            raise SystemExit("model call failed: incomplete_response")

        prd_id, _ = self.start(first)
        run_id = f"web-{prd_id}"
        plain = self.client.post("/api/m2/runs", json={"prd_id": prd_id, "confirm_open_issues": True})
        self.assertEqual((plain.json()["created"], len(calls)), (False, 1))  # no silent rerun
        with patch.object(run, "main", lambda argv: calls.append(argv)):
            again = self.client.post("/api/m2/runs", json={"prd_id": prd_id, "confirm_open_issues": True,
                                                           "retry": True})
            web._threads[run_id].join(5)
        self.assertEqual(again.json(), {"run_id": run_id, "created": False})
        self.assertEqual(calls[1][:3], ["run", "--resume", str(Path(self.tmp.name) / run_id)])
        web_json = run.load(Path(self.tmp.name) / run_id / "web.json")
        self.assertEqual((web_json["status"], web_json["previous_errors"]),
                         ("finished", ["model call failed: incomplete_response"]))

    def test_failed_checks_and_interrupted_runs(self):
        def checks_failed(argv):
            run_id = argv[argv.index("--run-id") + 1]
            run.dump(Path(self.tmp.name) / run_id / "artifacts/verify.json",
                     {"status": "checks_failed", "passed": 1, "total": 3, "failures": ["CHK-002 failed"]})

        prd_id, _ = self.start(checks_failed)
        with patch.object(web, "preview_running", lambda run_id: False):
            status = self.client.get(f"/api/m2/runs/web-{prd_id}").json()
        self.assertEqual((status["outcome"], status["verify"]["failures"]), ("verification_failed", ["CHK-002 failed"]))
        web_json = Path(self.tmp.name) / f"web-{prd_id}" / "web.json"
        run.dump(web_json, {**run.load(web_json), "status": "running"})  # as if the server died mid-run
        web._threads.pop(f"web-{prd_id}")
        self.assertEqual(self.client.get(f"/api/m2/runs/web-{prd_id}").json()["outcome"], "interrupted")

    def test_manual_only_checks_are_not_reported_as_automatic_pass(self):
        def manual_only(argv):
            run_id = argv[argv.index("--run-id") + 1]
            run.dump(Path(self.tmp.name) / run_id / "artifacts/verify.json",
                     {"status": "manual_pending", "automatic_status": "not_applicable", "passed": 0, "total": 0,
                      "failures": []})

        prd_id, _ = self.start(manual_only)
        with patch.object(web, "preview_running", lambda run_id: False):
            status = self.client.get(f"/api/m2/runs/web-{prd_id}").json()
        self.assertEqual((status["outcome"], status["manual_review"]), ("manual_check_only", "pending"))

    def test_one_active_run_and_ids_are_validated(self):
        release = threading.Event()
        prd_id, _ = None, None
        with self.fake_complete(prd_output()):
            prd_id = self.client.post("/api/m2/prd", json={"state": approved_state().model_dump()}).json()["prd_id"]
        with patch.object(run, "main", lambda argv: release.wait(5)):
            self.client.post("/api/m2/runs", json={"prd_id": prd_id, "confirm_open_issues": True})
            other = approved_state().model_copy(update={"run_id": "analysis-2"})
            with self.fake_complete(prd_output()):
                other_id = self.client.post("/api/m2/prd", json={"state": other.model_dump()}).json()["prd_id"]
            busy = self.client.post("/api/m2/runs", json={"prd_id": other_id, "confirm_open_issues": True})
            release.set()
            web._threads[f"web-{prd_id}"].join(5)
        self.assertEqual(busy.status_code, 409)
        for bad in ("../../etc", "web-xyz", "web-../../a"):
            self.assertEqual(self.client.get(f"/api/m2/runs/{bad}").status_code in (404, 422), True)
        self.assertEqual(self.client.post("/api/m2/runs", json={"prd_id": "../x"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()

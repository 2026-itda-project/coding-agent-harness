import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pipeline as p
import run


def session(count=5):
    return p.initial({"user_input": "여러 기능의 데모", "requirements": [
        {"id": f"R{i}", "description": f"기능 {i}", "status": "confirmed", "source": "user_input"}
        for i in range(count)], "gaps": []})


def jev(choices, confidence=1):
    return {"model": run.MODEL, "answers": {key: {
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": {label: float(label == choice) for label in run.OPTIONS}}
        for key, choice in choices.items()}, "usage": {"input_tokens": 100, "output_tokens": 5}}


def writer(stage, state, schema):
    if stage == "review":
        return {key: "ai_refine" for key in state["target_ids"]}
    return {key: "모델이 작성한 내용 " + key for key in state["actions"]}


class PipelineTests(unittest.TestCase):
    def test_three_rounds_then_proposed_without_fourth_question(self):
        current = session()
        original = copy.deepcopy(current["document"])
        judge = lambda payload: jev({key: "ask_user" for key in payload["questions"]})
        for number in range(3):
            current = p.advance(current, "policy", judge, writer)
            self.assertEqual(len(current["pending_questions"]), 3)
            self.assertEqual(len(current["queued_target_ids"]), 2)
            if number == 1:
                self.assertTrue({"R3", "R4"} <= set(current["pending_questions"]))
            p.apply_answers(current, {key: "모르겠어요" for key in current["pending_questions"]})
            self.assertEqual(len(current["previous_rounds"]), number + 1)
        current = p.advance(current, "policy", judge, writer)
        self.assertEqual(current["phase"], "review")
        self.assertFalse(current["pending_questions"])
        self.assertEqual(len(current["proposals"]), 5)
        self.assertTrue(all(v["status"] == "proposed" and v["source"] == "ai_proposal"
                            and v["reason"] == "round_limit" for v in current["proposals"].values()))
        self.assertEqual(current["document"], original)
        with self.assertRaises(ValueError):
            p.apply_answers(current, {})

    def test_low_confidence_goes_to_sonnet_not_automatic_default(self):
        sonnet = Mock(side_effect=lambda stage, state, schema:
                      {"R0": "ask_user"} if stage == "review" else writer(stage, state, schema))
        result = p.advance(session(1), "policy", lambda _: jev({"R0": "ready"}, .59), sonnet)
        self.assertEqual(sonnet.call_args_list[0].args[0], "review")
        self.assertEqual(result["decision_audit"]["R0"]["reason"], "low_confidence")
        self.assertEqual(result["phase"], "awaiting_answers")
        self.assertFalse(result["proposals"])

    def test_ready_refine_and_spec_are_distinct(self):
        result = p.advance(session(3), "policy", lambda _: jev({
            "R0": "ready", "R1": "ai_refine", "R2": "defer_to_spec"}), writer)
        self.assertEqual(result["phase"], "review")
        self.assertEqual(set(result["proposals"]), {"R1"})
        self.assertEqual(set(result["spec_notes"]), {"R2"})
        self.assertEqual(result["document"]["requirements"][1]["status"], "confirmed")
        self.assertNotIn("usage", result["decision_audit"]["R1"]["jev"])

    def test_orphan_gap_and_shared_gap_once_with_valid_references(self):
        document = session(2)["document"]
        document["gaps"] = [
            {"id": "G1", "description": "공통 미결정", "related_requirement_ids": ["R0", "R1"]},
            {"id": "G2", "description": "독립 미결정", "related_requirement_ids": []}]
        targets = p.targets_for(document)
        self.assertEqual(set(targets), {"R0", "R1", "G2"})
        self.assertEqual(sum(len(t["gaps"]) for t in targets.values()), 2)
        document["gaps"][0]["related_requirement_ids"] = ["missing"]
        with self.assertRaises(ValueError):
            p.targets_for(document)

    def test_answer_history_is_sent_and_silence_is_not_an_answer(self):
        current = p.advance(session(1), "policy", lambda _: jev({"R0": "ask_user"}), writer)
        for answers in ({}, {"R0": ""}, {"bad": "yes"}):
            with self.assertRaises(ValueError):
                p.apply_answers(current, answers)
        p.apply_answers(current, {"R0": "내가 직접 승인할게"})
        payload = p.request_for(current, "policy")
        self.assertEqual(payload["state"]["previous_rounds"][0]["answers"]["R0"], "내가 직접 승인할게")
        result = p.advance(current, "policy", lambda _: jev({"R0": "ready"}), writer)
        self.assertEqual(result["phase"], "review")

    def test_invalid_jev_falls_back_and_invalid_sonnet_stops(self):
        sonnet = Mock(side_effect=writer)
        result = p.advance(session(1), "policy", lambda _: {"answers": {}}, sonnet)
        self.assertEqual(result["decision_audit"]["R0"]["reason"], "jev_failed")
        for bad in ({"R0": "invented"}, {"wrong": "ready"}, {"R0": []}):
            with self.assertRaises(ValueError):
                p.advance(session(1), "policy", lambda _: {}, lambda *args: bad)
        with self.assertRaises(ValueError):
            p.advance(session(1), "policy", lambda _: jev({"R0": "ai_refine"}), lambda *args: {"R0": ""})

    def test_retry_is_bounded_and_only_transient_jev_errors(self):
        log = Mock()
        provider = p.Providers("policy", "claude-sonnet-test", log)
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "secret"}), patch.object(p.time, "sleep") as sleep:
            with patch.object(run, "call_api", side_effect=RuntimeError("http_429")) as call:
                with self.assertRaises(RuntimeError):
                    provider.jev({})
                self.assertEqual(call.call_count, 3)
                self.assertEqual(sleep.call_count, 2)
            with patch.object(run, "call_api", side_effect=RuntimeError("http_401")) as call:
                with self.assertRaises(RuntimeError):
                    provider.jev({})
                self.assertEqual(call.call_count, 1)

    def test_sonnet_http_contract_and_incomplete_output(self):
        events = []
        provider = p.Providers("policy", "claude-sonnet-test", events.append)
        raw = {"model": "claude-sonnet-test", "stop_reason": "end_turn",
               "content": [{"type": "thinking", "thinking": "reasoning"}, {"type": "text", "text": '{"R0": "ready"}'}],
               "usage": {"input_tokens": 10, "output_tokens": 5}}
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "PRIVATE_KEY"}), patch.object(p, "build_opener") as opener:
            def prepare(value):
                opener.return_value.open.return_value.__enter__.return_value = io.StringIO(json.dumps(value))
            prepare(raw)
            self.assertEqual(provider.sonnet("review", {}, p.object_schema({"R0": p.TEXT})), {"R0": "ready"})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, p.SONNET_ENDPOINT)
            self.assertIn("output_config", json.loads(request.data))
            for mutation in ({"stop_reason": "max_tokens"}, {"model": "other"},
                             {"content": [{"type": "thinking", "thinking": "only reasoning"}]},
                             {"content": [{"type": "text", "text": "{}"}] * 2},
                             {"content": [{"type": "text", "text": "not json"}]}):
                prepare({**raw, **mutation})
                with self.assertRaisesRegex(RuntimeError, "sonnet_failed"):
                    provider.sonnet("review", {}, {})
        self.assertNotIn("PRIVATE_KEY", json.dumps(events))

    def test_cli_extracts_when_only_utterance_and_failure_writes_no_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            source.write_text(json.dumps({"user_input": "데모를 만들어줘"}))
            env = {"TYPESAFE_API_KEY": "secret", "ANTHROPIC_API_KEY": "secret", "SONNET_MODEL": "claude-sonnet-test"}
            def sonnet(stage, state, schema):
                if stage == "extract":
                    document = session(1)["document"]
                    return {key: document[key] for key in ("requirements", "gaps")}
                return writer(stage, state, schema)
            with patch.dict("os.environ", env), patch.object(p.Providers, "jev", return_value=jev({"R0": "ready"})), patch.object(p.Providers, "sonnet", side_effect=sonnet), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(p.main(["--live", "--input", str(source), "--out", str(root / "success")]), 0)
            saved = json.loads((root / "success/session.json").read_text())["session"]
            self.assertEqual(saved["document"]["user_input"], "데모를 만들어줘")
            self.assertEqual(saved["phase"], "review")
            with patch.dict("os.environ", env), patch.object(p.Providers, "sonnet", side_effect=RuntimeError("failed")), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(p.main(["--live", "--input", str(source), "--out", str(root / "failure")]), 1)
            self.assertFalse((root / "failure/session.json").exists())

    def test_cli_dry_run_no_network_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "dry"
            args = ["--input", str(run.ROOT / "pipeline-input.json"), "--out", str(out)]
            with patch.object(p.Providers, "jev", side_effect=AssertionError), patch.object(p.Providers, "sonnet", side_effect=AssertionError), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(p.main(args), 0)
                with self.assertRaises(FileExistsError):
                    p.main(args)
            self.assertFalse((out / "session.json").exists())
            self.assertEqual(json.loads((out / "calls.jsonl").read_text().splitlines()[-1])["billable_calls"], 0)

    def test_cli_live_resume_with_mock_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            source.write_text(json.dumps(session(1)["document"]))
            env = {"TYPESAFE_API_KEY": "secret", "ANTHROPIC_API_KEY": "secret", "SONNET_MODEL": "claude-sonnet-test"}
            with patch.dict("os.environ", env), patch.object(p.Providers, "jev", return_value=jev({"R0": "ask_user"})), patch.object(p.Providers, "sonnet", side_effect=writer), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(p.main(["--live", "--input", str(source), "--out", str(root / "first")]), 0)
                answers = root / "answers.json"
                answers.write_text(json.dumps({"R0": "모르겠어요"}))
                self.assertEqual(p.main(["--live", "--resume", str(root / "first/session.json"), "--answers", str(answers), "--out", str(root / "second")]), 0)
            saved = json.loads((root / "second/session.json").read_text())["session"]
            self.assertEqual(len(saved["previous_rounds"]), 1)
            self.assertNotIn("secret", (root / "second/session.json").read_text())


if __name__ == "__main__":
    unittest.main()

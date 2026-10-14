import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import run


def response(choice="ask_user", usage=True):
    raw = {"model": run.MODEL, "answers": {"decision": {
        "type": "choice", "choice": choice,
        "probabilities": {k: float(k == choice) for k in run.OPTIONS},
        "confidence": 1.0,
    }}}
    if usage:
        raw["usage"] = {"input_tokens": 100, "output_tokens": 10}
    return raw


class PilotTests(unittest.TestCase):
    def test_expected_answer_and_reason_are_not_sent(self):
        case = run.load_cases()[0]
        case.update(expected="SECRET_LABEL", rationale="SECRET_REASON", id="SECRET_ID", category="SECRET_CATEGORY")
        payload = json.dumps(run.make_request(case, "test policy"))
        self.assertNotIn("SECRET_", payload)
        self.assertIn("user_input", payload)

    def test_contract_rejects_invalid_answers(self):
        valid = response()
        mutations = [
            {"choice": "invented"}, {"type": "noul"}, {"confidence": float("nan")},
            {"probabilities": {"ask_user": 1}},
            {"probabilities": {k: 0.1 for k in run.OPTIONS}},
            {"choice": "ready"},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                raw = copy.deepcopy(valid)
                raw["answers"]["decision"].update(mutation)
                with self.assertRaises(ValueError):
                    run.parse_response(raw)
        self.assertEqual(run.parse_response(valid)["decision"], "ask_user")
        with self.assertRaises(ValueError):
            run.parse_response({**valid, "model": "unexpected-version"})

    def test_rounded_probability_sum_is_preserved_but_large_error_rejected(self):
        raw = response('ready')
        raw['answers']['decision']['probabilities'] = {'ready':.93,'ai_refine':.02,'ask_user':0.,'defer_to_spec':.04}
        parsed = run.parse_response(raw)
        self.assertAlmostEqual(sum(parsed['probabilities'].values()), .99)
        raw['answers']['decision']['probabilities']['ready'] = .85
        with self.assertRaises(ValueError):
            run.parse_response(raw)

    def test_unknown_usage_is_not_zero(self):
        parsed = run.parse_response(response(usage=False))
        summary = run.summarize([{"status": "ok", "expected": "ask_user", "latency_ms": 1, **parsed}])
        self.assertIsNone(summary["input_tokens"])
        self.assertIsNone(summary["cost_usd"])
        self.assertIsNone(summary["question_rounds"])

    def test_wrong_decisions_change_metrics(self):
        records = []
        for expected, predicted in [("ask_user", "ai_refine"), ("ai_refine", "ask_user"), ("ready", "ready")]:
            records.append({"status": "ok", "expected": expected, "latency_ms": 1,
                            **run.parse_response(response(predicted))})
        summary = run.summarize(records)
        self.assertAlmostEqual(summary["accuracy_valid_responses"], 1 / 3)
        self.assertEqual(summary["user_confirmation_recall_valid_responses"], 0)
        self.assertEqual(summary["unnecessary_question_rate_valid_responses"], 0.5)

    def test_dry_run_never_calls_api_or_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dry.jsonl"
            with patch.object(run, "call_api", side_effect=AssertionError("network forbidden")), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(run.main(["--out", str(output)]), 0)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len([r for r in rows if r["type"] == "request"]), 60)
            self.assertEqual(rows[0]["split"], "dev")
            self.assertEqual(rows[-1]["attempted_calls"], 0)
            self.assertIsNone(rows[-1]["accuracy_valid_responses"])
            with self.assertRaises(FileExistsError):
                run.main(["--out", str(output)])

    def test_live_contract_failure_is_logged_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "contract-test.jsonl"
            with patch.dict("os.environ", {"TYPESAFE_API_KEY": "PRIVATE_TEST_KEY"}), patch.object(run, "call_api", return_value={"unexpected": True}) as fake, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(run.main(["--live", "--out", str(output)]), 1)
                self.assertEqual(fake.call_count, 1)
            text = output.read_text()
            rows = [json.loads(line) for line in text.splitlines()]
            self.assertNotIn("PRIVATE_TEST_KEY", text)
            self.assertEqual(rows[-2]["response"], {"unexpected": True})
            self.assertEqual(rows[-1]["errors"], 1)
            self.assertEqual(rows[-1]["completed"], 0)
            self.assertIsNone(rows[-1]["accuracy_valid_responses"])

    def test_http_failure_does_not_expose_key_or_server_body(self):
        error = HTTPError(run.ENDPOINT, 401, "PRIVATE_TEST_KEY", {}, io.BytesIO(b"PRIVATE_TEST_KEY"))
        with patch.object(run, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(RuntimeError, "^http_401$"):
                run.call_api({"state": "synthetic"}, "PRIVATE_TEST_KEY")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.typesafe.ai/v1/systemone")
            self.assertNotIn(b"PRIVATE_TEST_KEY", request.data)

    def test_redirect_does_not_forward_authorization(self):
        self.assertIsNone(run.NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "https://other.example/"))

    def test_category_filter_applies_before_limit_and_never_calls_api(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "category.jsonl"
            with patch.object(run, "call_api", side_effect=AssertionError("network forbidden")), patch("sys.stdout", new_callable=io.StringIO):
                run.main(["--category", "implicit_core_gap", "--limit", "2", "--out", str(output)])
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(rows[0]["selected_case_ids"], ["CORE-01", "CORE-02"])
            self.assertEqual(rows[0]["category_counts"], {"implicit_core_gap": 2})
            self.assertEqual(set(rows[-1]["by_category"]), {"implicit_core_gap"})
            self.assertEqual(rows[-1]["attempted_calls"], 0)
            self.assertIsNone(rows[-1]["by_category"]["implicit_core_gap"]["accuracy_valid_responses"])

    def test_category_metrics_expose_failure_hidden_by_total_accuracy(self):
        cases = [c for c in run.load_cases() if c["split"] == "dev"]
        fake_responses = [response(c["expected"]) for c in cases]
        fake_responses[0] = response("ai_refine")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "grouped.jsonl"
            with patch.dict("os.environ", {"TYPESAFE_API_KEY": "PRIVATE_TEST_KEY"}), patch.object(run, "call_api", side_effect=fake_responses), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(run.main(["--live", "--out", str(output)]), 0)
            summary = json.loads(output.read_text().splitlines()[-1])
            self.assertEqual(summary["accuracy_valid_responses"], 59 / 60)
            groups = summary["by_category"]
            self.assertAlmostEqual(groups["implicit_core_gap"]["user_confirmation_recall_valid_responses"], 9 / 10)
            self.assertEqual(groups["context_resolved"]["accuracy_valid_responses"], 1)
            self.assertEqual(groups["unsupported_scope"]["attempted_calls"], 10)

    def test_refinement_and_user_question_are_different_outputs(self):
        expectations = {"ready": (False, False), "ai_refine": (True, False), "ask_user": (True, True), "defer_to_spec": (False, False)}
        for choice, expected in expectations.items():
            with self.subTest(choice=choice):
                parsed = run.parse_response(response(choice))
                self.assertEqual((parsed["needs_refinement"], parsed["requires_user_input"]), expected)

    def test_dataset_has_twenty_per_category_and_disjoint_domains(self):
        cases = run.load_cases()
        self.assertEqual(len(cases), 120)
        self.assertEqual(len({c["user_input"] for c in cases}), 120)
        for category in run.CATEGORIES:
            self.assertEqual(sum(c["category"] == category for c in cases), 20)
            for split in ("dev", "test"):
                self.assertEqual(sum(c["category"] == category and c["split"] == split for c in cases), 10)
        self.assertEqual(sum(c["input_style"] == "explicit_indecision" for c in cases), 2)
        self.assertFalse({c["domain"] for c in cases if c["split"] == "dev"} & {c["domain"] for c in cases if c["split"] == "test"})

    def test_test_split_dry_run_excludes_development_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test-split.jsonl"
            with patch.object(run, "call_api", side_effect=AssertionError("network forbidden")), patch("sys.stdout", new_callable=io.StringIO):
                run.main(["--split", "test", "--category", "context_resolved", "--out", str(output)])
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(rows[0]["selected_case_ids"], ["CONTEXT-" + str(i) for i in range(11, 21)])
            self.assertTrue(all(r["split"] == "test" for r in rows if r["type"] == "request"))


if __name__ == "__main__":
    unittest.main()

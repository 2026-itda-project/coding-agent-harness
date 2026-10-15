"""Offline tests for the M2 E2E path. No model, Claude Code or Docker calls."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.llm import claude
from app.llm.errors import AnalysisError
from app.m2 import agent, run, stages
from app.m2.context import build_context
from app.m2.contracts import (PRD, Plan, Spec, check_plan, check_prd, check_spec, order_tasks,
                              output_schema)


def item(id_, status="confirmed", source="initial_input"):
    return {"id": id_, "description": f"{id_} 설명", "status": status, "source": source}


def prd_data():
    return {"title": "데모", "overview": {"summary": "요약", "problem": "문제", "goals": ["목표"]},
            "in_scope": [item("SCOPE-001")], "out_of_scope": [item("OUT-001", source="harness_policy")],
            "roles": [item("ROLE-001")], "user_flows": [item("US-001")],
            "functional_requirements": [item("FR-001"), item("FR-002", "proposed", "ai_proposal")],
            "business_rules": [item("RULE-001")], "nfrs": [],
            "acceptance_criteria": [{"id": "AC-001", "description": "된다", "target_ids": ["FR-001"]},
                                    {"id": "AC-002", "description": "된다", "target_ids": ["FR-002"]}],
            "open_issues": []}


def spec_data():
    step = {"method": "GET", "path": "/api/items", "role_header": "", "body_json": "", "expect_status": 200,
            "expect_contains": []}
    return {"screens": [{"id": "SCR-001", "name": "목록", "purpose": "보기", "role_ids": ["ROLE-001"],
                         "elements": ["표"], "requirement_ids": ["FR-001"]}],
            "data_models": [], "permissions": [], "mock_integrations": [],
            "apis": [{"id": "API-001", "method": "GET", "path": "/api/items", "description": "목록",
                      "request": "", "response": "[]", "requirement_ids": ["FR-001", "FR-002"]}],
            "rule_implementations": [{"id": "IMPL-001", "description": "규칙", "where": ["API-001"],
                                      "requirement_ids": ["RULE-001"]}],
            "checks": [{"id": "CHK-001", "ac_ids": ["AC-001", "AC-002"], "description": "목록",
                        "http_steps": [step], "rerun_after_restart": True, "manual_steps": []}]}


def plan_data():
    task = {"feature_id": "FEAT-001", "title": "t", "instructions": "do", "spec_ids": ["API-001"],
            "files": ["backend/main.py"], "acceptance": ["ok"], "check_ids": []}
    return {"features": [{"id": "FEAT-001", "name": "f", "spec_ids": ["API-001"]}],
            "tasks": [{**task, "id": "TASK-002", "depends_on": ["TASK-001"], "check_ids": ["CHK-001"]},
                      {**task, "id": "TASK-001", "depends_on": []}]}


class ContractTest(unittest.TestCase):
    def test_valid_chain_passes_and_orders_tasks(self):
        prd, spec, plan = PRD.model_validate(prd_data()), Spec.model_validate(spec_data()), Plan.model_validate(plan_data())
        self.assertEqual(check_prd(prd), [])
        self.assertEqual(check_spec(spec, prd), [])
        self.assertEqual(check_plan(plan, spec), [])
        self.assertEqual(order_tasks(plan), ["TASK-001", "TASK-002"])

    def test_prd_errors(self):
        data = prd_data()
        data["functional_requirements"].append(item("FR-001"))                      # duplicate
        data["roles"].append(item("FR-009"))                                        # wrong prefix
        data["user_flows"].append(item("US-002", "proposed", "initial_input"))      # proposed w/o AI source
        data["nfrs"].append(item("NFR-001", "needs_clarification"))                 # undecided outside open_issues
        data["functional_requirements"].append(item("FR-003"))                      # no AC
        errors = " | ".join(check_prd(PRD.model_validate(data)))
        for part in ("duplicate id FR-001", "FR-009 must start with ROLE-", "US-002", "NFR-001", "FR-003 has no"):
            self.assertIn(part, errors)

    def test_spec_reference_errors(self):
        prd = PRD.model_validate(prd_data())
        data = spec_data()
        data["apis"][0]["requirement_ids"] = ["FR-404"]
        data["apis"][0]["path"] = "/items"
        data["checks"][0]["ac_ids"] = ["AC-001"]
        data["screens"][0]["role_ids"] = ["FR-001"]                                 # not a role
        errors = " | ".join(check_spec(Spec.model_validate(data), prd))
        for part in ("FR-404", "must start with /api/", "AC-002 not covered", "FR-002 not referenced",
                     "SCR-001 unknown role FR-001"):
            self.assertIn(part, errors)

    def test_plan_cycle_and_unknown_refs(self):
        spec = Spec.model_validate(spec_data())
        data = plan_data()
        data["tasks"][1]["depends_on"] = ["TASK-002"]
        self.assertIn("cycle", " ".join(check_plan(Plan.model_validate(data), spec)))
        data = plan_data()
        data["tasks"][0]["spec_ids"] = ["API-999"]
        data["tasks"][0]["check_ids"] = []
        errors = " ".join(check_plan(Plan.model_validate(data), spec))
        self.assertIn("API-999", errors)
        self.assertIn("CHK-001 not assigned", errors)

    def test_output_schema_is_inline_and_closed(self):
        schema = output_schema(Spec)
        text = json.dumps(schema)
        self.assertNotIn("$ref", text)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(Spec.model_fields))
        for model in (PRD, Plan):  # fields named "title" must survive metadata stripping
            s = output_schema(model)
            self.assertEqual(set(s["required"]), set(s["properties"]))
        self.assertIn("title", output_schema(Plan)["properties"]["tasks"]["items"]["properties"])


class ClaudeParseTest(unittest.TestCase):
    def raw(self, **kw):
        base = {"model": "claude-opus-5-5", "stop_reason": "end_turn",
                "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"a": 1}'}]}
        return {**base, **kw}

    def test_thinking_blocks_are_ignored(self):
        self.assertEqual(claude.parse(self.raw(), "claude-opus-5-5"), {"a": 1})

    def test_rejects_wrong_model_incomplete_and_extra_text(self):
        for raw in (self.raw(model="claude-sonnet-5-5"), self.raw(stop_reason="max_tokens"),
                    self.raw(content=[{"type": "text", "text": "{}"}, {"type": "text", "text": "{}"}])):
            with self.assertRaises(AnalysisError):
                claude.parse(raw, "claude-opus-5-5")

    def test_unconstrained_reply_fence_is_trimmed(self):
        for text in ('```json\n{"a": 2}\n```', '```{"a": 2}```'):
            raw = self.raw(content=[{"type": "text", "text": text}])
            self.assertEqual(claude.parse(raw, "claude-opus-5-5"), {"a": 2})

    def test_budget_stops_before_overspending(self):
        with patch.dict(os.environ, {"OPUS_MODEL": "claude-opus-5-5"}):
            budget = run.Budget(1.0, [{"cost_usd": 0.5}])
            budget.reserve("opus", max_output=10_000, max_input=10_000)   # 0.5 + 0.24 <= 1.0
            with self.assertRaises(SystemExit):
                budget.reserve("opus", max_output=30_000, max_input=10_000)  # 0.5 + 0.64 > 1.0


    def test_cost_uses_price_table(self):
        self.assertAlmostEqual(claude.cost("claude-opus-5-5", {"input_tokens": 1_000_000, "output_tokens": 100_000}), 6.0)

    def test_generate_records_usage_and_rejects_bad_contract(self):
        bad = prd_data()
        bad["acceptance_criteria"] = []

        def fake(model, system, payload, schema, log, **kw):
            log.append({"model": model, "cost_usd": 0.01})
            return bad

        log = []
        with patch.dict(os.environ, {"SONNET_MODEL": "claude-sonnet-5-5"}), patch.object(claude, "complete", fake):
            with self.assertRaises(stages.ContractError) as ctx:
                stages.generate_prd("입력", log)
        self.assertEqual(log[0]["stage"], "prd")
        self.assertIn("FR-001 has no acceptance criterion", ctx.exception.errors)


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = agent.init_workspace(Path(self.tmp.name) / "app")

    def test_workspace_is_local_git_outside_harness(self):
        self.assertEqual(agent.git(self.app, "remote").strip(), "")
        (self.app / "node_modules").mkdir()
        (self.app / "node_modules/x.js").write_text("x")
        (self.app / "data").mkdir()
        (self.app / "data/app.db").write_text("db")
        agent.git(self.app, "add", "-A")
        self.assertEqual(agent.git(self.app, "status", "--porcelain"), "")  # excluded even without .gitignore
        with self.assertRaises(ValueError):
            agent.init_workspace(agent.HARNESS_REPO / "tmp-workspace")

    def test_context_has_sources_and_skips_secrets(self):
        (self.app / "backend").mkdir()
        (self.app / "backend/main.py").write_text("app = 1\n")
        (self.app / ".env").write_text("SECRET=x\n")
        (self.app / "node_modules").mkdir()
        (self.app / "node_modules/x.js").write_text("x")
        agent.git(self.app, "add", "-A")
        agent.git(self.app, "commit", "-q", "-m", "files")
        prd, spec, plan = PRD.model_validate(prd_data()), Spec.model_validate(spec_data()), Plan.model_validate(plan_data())
        task = plan.tasks[0]
        ctx = build_context(task, prd, spec, self.app, {"TASK-001": {"task_id": "TASK-001"}}, "/sandbox.sh")
        self.assertEqual(ctx["repository"]["files"], ["backend/main.py"])
        self.assertIn("API-001", ctx["spec_items"])
        self.assertIn("CHK-001", ctx["checks"])
        self.assertEqual(set(ctx["requirements"]), {"FR-001", "FR-002"})
        self.assertEqual(ctx["previous_tasks"], [{"task_id": "TASK-001"}])
        self.assertIn("prd.json#AC-001", ctx["sources"]["requirements"])

    def run_fake(self, is_error, write=True):
        def runner(command, cwd, **kw):
            self.assertIn("--bare", command)
            self.assertIn(f"Bash({agent.SANDBOX}:*)", command)
            if write:
                (Path(cwd) / "out.txt").write_text(str(is_error))
            out = json.dumps({"is_error": is_error, "result": "요약", "total_cost_usd": 0.2, "num_turns": 3})
            return subprocess.CompletedProcess(command, 0, out, "")

        task = Plan.model_validate(plan_data()).tasks[1]
        with patch.dict(os.environ, {"SONNET_MODEL": "claude-sonnet-5-5"}):
            return agent.run_task(task, {"task": "x"}, self.app, 1.0, runner=runner)

    def test_task_commit_and_failure_status(self):
        record = self.run_fake(False)
        self.assertEqual(record["status"], "done")
        self.assertEqual(record["files"], ["A\tout.txt"])
        self.assertIn("TASK-001", agent.git(self.app, "log", "-1", "--format=%s"))
        self.assertEqual(self.run_fake(True)["status"], "failed")
        self.assertEqual(self.run_fake(False, write=False)["status"], "failed")


if __name__ == "__main__":
    unittest.main()

"""Offline tests for the M2 E2E path. No model, Claude Code or Docker calls. POSIX only, like the runner."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.llm import claude
from app.llm.errors import AnalysisError
from app.m2 import agent, run, stages, verify
from app.m2.context import build_context
from app.m2.contracts import (PRD, Plan, Spec, check_generated_prd, check_plan, check_prd, check_spec,
                              order_tasks, output_schema)


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

    def test_status_source_combinations(self):
        data = prd_data()
        data["functional_requirements"].append(item("FR-003", "confirmed", "ai_proposal"))      # accepted in Review
        data["acceptance_criteria"].append({"id": "AC-003", "description": "된다", "target_ids": ["FR-003"]})
        data["open_issues"] = [item("ISSUE-001", "needs_clarification", "initial_input"),
                               item("ISSUE-002", "needs_clarification", "ai_proposal")]
        prd = PRD.model_validate(data)
        self.assertEqual(check_prd(prd), [])
        self.assertEqual(check_generated_prd(prd), ["FR-003: confirmed ai_proposal without user acceptance"])
        bad = prd_data()
        bad["roles"].append(item("ROLE-002", "proposed", "initial_input"))
        bad["nfrs"].append(item("NFR-001", "proposed", "harness_policy"))
        errors = " | ".join(check_prd(PRD.model_validate(bad)))
        self.assertIn("ROLE-002: proposed status requires ai_proposal", errors)
        self.assertIn("NFR-001", errors)

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
            budget = run.Budget(1.0, lambda: [0.5])
            budget.reserve("opus", max_output=10_000, max_input=10_000)   # 0.5 + 0.24 <= 1.0
            with self.assertRaises(SystemExit):
                budget.reserve("opus", max_output=30_000, max_input=10_000)  # 0.5 + 0.64 > 1.0
            with self.assertRaises(SystemExit):  # an unknown cost is never counted as 0
                run.Budget(10.0, lambda: [0.1, None]).reserve("opus")


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

    def test_planted_git_hooks_never_run_on_host(self):
        self.assertFalse((self.app / ".git").exists())  # repository lives outside the work tree
        marker = Path(self.tmp.name) / "hook-ran"
        hooks = self.app / ".git/hooks"
        hooks.mkdir(parents=True)
        for hook in ("pre-commit", "post-commit"):
            (hooks / hook).write_text(f"#!/bin/sh\ntouch {marker}\n")
            (hooks / hook).chmod(0o755)
        record = self.run_fake(False)
        self.assertEqual(record["status"], "done")
        self.assertFalse(marker.exists())
        self.assertNotIn(".git/hooks/pre-commit", " ".join(record["files"]))

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

    def test_context_skips_links_to_outside_files(self):
        secret = Path(self.tmp.name) / "outside-secret.txt"
        secret.write_text("TOP-SECRET")
        (self.app / "key.txt").symlink_to(secret)
        (self.app / "ok.txt").write_text("fine")
        agent.git(self.app, "add", "-A")
        agent.git(self.app, "commit", "-q", "-m", "link")
        prd, spec, plan = PRD.model_validate(prd_data()), Spec.model_validate(spec_data()), Plan.model_validate(plan_data())
        ctx = build_context(plan.tasks[0], prd, spec, self.app, {}, "/sandbox.sh")
        self.assertNotIn("TOP-SECRET", json.dumps(ctx))
        self.assertEqual(ctx["repository"]["skipped"], {"key.txt": "symlink"})
        self.assertEqual(ctx["repository"]["contents"]["ok.txt"], "fine")

    def run_fake(self, is_error, write=True, timeout=False):
        def runner(command, timeout_s, cwd, env):
            self.assertIn("--bare", command)
            self.assertIn(f"Bash({agent.SANDBOX}:*)", command)
            self.assertEqual(env["HARNESS_RUN_ID"], agent.run_id_of(self.app))
            if write:
                (Path(cwd) / "out.txt").write_text(str(is_error))
            if timeout:
                return None, "", "killed"
            return 0, json.dumps({"is_error": is_error, "result": "요약", "total_cost_usd": 0.2, "num_turns": 3}), ""

        task = Plan.model_validate(plan_data()).tasks[1]
        with patch.dict(os.environ, {"SONNET_MODEL": "claude-sonnet-5-5"}), \
                patch.object(agent, "cleanup", return_value=["left-behind"]) as cleanup:
            record = agent.run_task(task, {"task": "x"}, self.app, 1.0, runner=runner)
        cleanup.assert_called_once_with(agent.run_id_of(self.app))
        return record

    def test_agent_timeout_fails_and_cleans_up(self):
        record = self.run_fake(False, timeout=True)
        self.assertEqual(record["status"], "failed")
        self.assertIsNone(record["exit_code"])
        self.assertEqual(record["agent"]["result"], "agent timeout")
        self.assertEqual(record["leftover_containers"], ["left-behind"])

    def test_task_commit_and_failure_status(self):
        record = self.run_fake(False)
        self.assertEqual(record["status"], "done")
        self.assertEqual(record["files"], ["A\tout.txt"])
        self.assertIn("TASK-001", agent.git(self.app, "log", "-1", "--format=%s"))
        self.assertEqual(self.run_fake(True)["status"], "failed")
        self.assertEqual(self.run_fake(False, write=False)["status"], "failed")


if __name__ == "__main__":
    unittest.main()


class VerifyJudgeTest(unittest.TestCase):
    def record(self, after_root=200, persist_passed=True, first_passed=True):
        check = lambda cid, ok: {"id": cid, "passed": ok}
        return {"root_status": 200, "root_status_after_restart": after_root,
                "checks": [check("CHK-001", first_passed)],
                "checks_after_restart": [check("CHK-001", persist_passed)] if after_root == 200 else []}

    def test_restart_failure_and_unrun_checks_fail(self):
        spec = Spec.model_validate(spec_data())  # one check, rerun after restart -> 2 planned
        self.assertEqual(verify.judge(self.record(), spec)["status"], "passed")
        failed = verify.judge(self.record(after_root=None), spec)
        self.assertEqual(failed["status"], "checks_failed")
        self.assertIn("1 planned checks did not run", failed["failures"])
        self.assertEqual(verify.judge(self.record(persist_passed=False), spec)["failures"], ["CHK-001 failed"])


class ResumeAttemptsTest(unittest.TestCase):
    def test_every_attempt_is_kept_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"SONNET_MODEL": "claude-sonnet-5-5",
                                                                           "OPUS_MODEL": "claude-opus-5-5"}):
            run_dir = Path(tmp) / "run"
            app = agent.init_workspace(run_dir / "app")
            art = run_dir / "artifacts"
            for name, data in (("prd", prd_data()), ("spec", spec_data()), ("plan", plan_data())):
                run.dump(art / f"{name}.json", data)
            run.dump(run_dir / "run.json", {"run_id": "t", "stages": {}, "calls": [], "tasks": {}, "attempts": []})

            def failing(task, context, app_dir, budget):
                (Path(app_dir) / f"try-{len(list(app_dir.iterdir()))}.txt").write_text("x")
                agent.git(app_dir, "add", "-A")
                base = agent.git(app_dir, "rev-parse", "HEAD").strip()
                agent.git(app_dir, "commit", "-q", "-m", "try")
                return {"task_id": task.id, "title": task.title, "status": "failed", "base_commit": base,
                        "commit": agent.git(app_dir, "rev-parse", "HEAD").strip(), "files": [],
                        "agent": {"total_cost_usd": 0.4}}

            args = ["run", "--resume", str(run_dir), "--until", "code", "--budget-usd", "1.0", "--task-budget-usd", "0.3"]
            with patch.object(agent, "run_task", failing):
                for _ in range(2):
                    with self.assertRaises(SystemExit):
                        run.main(args)
                with self.assertRaises(SystemExit) as stop:  # 0.8 spent + 0.3 reserve > 1.0
                    run.main(args)
            self.assertIn("budget stop", str(stop.exception))
            state = run.load(run_dir / "run.json")
            self.assertEqual([a["attempt"] for a in state["attempts"]], [1, 2])
            self.assertEqual(state["cost_usd"]["agent"], 0.8)
            self.assertEqual(state.get("previous_errors", []), [])
            for n in (1, 2):
                self.assertTrue((art / f"agent/TASK-001-attempt-{n}.json").exists())
                self.assertTrue((art / f"diffs/TASK-001-attempt-{n}.diff").exists())


class ReviewFixesTest(unittest.TestCase):
    def test_manual_only_and_mixed_checks_remain_pending(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                data = spec_data()
                data["checks"][0]["manual_steps"] = ["화면을 확인한다"]
                if not mixed:
                    data["checks"][0]["http_steps"] = []
                spec = Spec.model_validate(data)
                self.assertEqual(check_spec(spec, PRD.model_validate(prd_data())), [])
                with patch.object(verify, "run_step", return_value={"passed": True}) as step:
                    first = verify.run_checks("unused", spec.checks)
                    after = verify.run_checks("unused", spec.checks)
                self.assertEqual(step.call_count, 2 if mixed else 0)
                result = verify.judge({"root_status": 200, "root_status_after_restart": 200,
                                       "checks": first, "checks_after_restart": after}, spec)
                self.assertEqual(result["status"], "manual_pending")
                self.assertEqual(result["automatic_status"], "passed" if mixed else "not_applicable")
                self.assertEqual(result["total"], 2 if mixed else 0)
                self.assertEqual(result["failures"], [])
                self.assertEqual(result["manual_checks"][0]["status"], "pending")
                self.assertEqual(result["manual_checks"][0]["steps"], data["checks"][0]["manual_steps"])
                result["root_status_after_restart"] = None
                self.assertEqual(verify.judge(result, spec)["status"], "checks_failed")

    def test_http_path_must_start_with_slash(self):
        for path in ("api/items", "", "https://example.com"):
            data = spec_data()
            data["checks"][0]["http_steps"][0]["path"] = path
            self.assertIn("CHK-001 HTTP step path must start with /",
                          check_spec(Spec.model_validate(data), PRD.model_validate(prd_data())))

    def test_cost_failure_preserves_usage_and_blocks_next_call(self):
        import io
        from unittest.mock import MagicMock
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 1}
        raw = {"model": "claude-sonnet-5-5", "usage": usage, "stop_reason": "end_turn"}
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = io.StringIO(json.dumps(raw))
        log = []
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-not-sent"}), \
                patch.object(claude, "build_opener", return_value=opener):
            with self.assertRaises(AnalysisError):
                claude.complete("claude-sonnet-5-5", "test", {}, {}, log)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["usage"], usage)
        self.assertIsNone(log[0]["cost_usd"])
        with self.assertRaises(SystemExit):
            run.Budget(100, lambda: [r["cost_usd"] for r in log]).check(1, "next")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie is dead, only not reaped yet
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:  # reaped meanwhile; without /proc (macOS) trust os.kill
        return not Path("/proc").is_dir()


class SandboxTest(unittest.TestCase):
    """#9 without Docker: process groups, sandbox.sh arguments, and how verify records failures."""

    def test_run_group_returns_output_and_kills_children_on_timeout(self):
        self.assertEqual(agent.run_group(["bash", "-c", "echo hi; echo err >&2; exit 3"], 10), (3, "hi\n", "err\n"))
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "pid"
            code, _, _ = agent.run_group(["bash", "-c", f"sleep 60 & echo $! > {pid_file}; wait"], 1)
            self.assertIsNone(code)
            pid = int(pid_file.read_text())
            for _ in range(50):
                if not alive(pid):
                    break
                time.sleep(0.1)
            self.assertFalse(alive(pid), "a child of the timed-out command survived")

    def test_run_id_of_a_relative_app_dir(self):
        self.assertEqual(agent.run_id_of(Path("app")), Path.cwd().name)  # `--resume .` inside the run folder

    def test_cleanup_gives_up_when_docker_hangs(self):
        with tempfile.TemporaryDirectory() as tmp:
            docker = Path(tmp) / "docker"
            docker.write_text("#!/bin/sh\nsleep 60\n")
            docker.chmod(0o755)
            with patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}"}), \
                    patch.object(agent, "DOCKER_TIMEOUT_S", 1):
                started = time.monotonic()
                self.assertEqual(agent.cleanup("run-x"), [])
            self.assertLess(time.monotonic() - started, 10)

    def test_dump_replaces_the_file_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run" / "run.json"
            run.dump(path, {"stage": "spec"})
            run.dump(path, {"stage": "계획"})
            self.assertEqual(run.load(path), {"stage": "계획"})
            self.assertEqual([p.name for p in path.parent.iterdir()], ["run.json"])  # no temporary file left

    def test_python_exit_stops_groups_left_running_in_threads(self):
        # The web server runs the agent in a daemon thread: Ctrl+C ends only the main thread.
        with tempfile.TemporaryDirectory() as tmp:
            pid_file, cleaned = Path(tmp) / "pid", Path(tmp) / "cleaned"
            script = "\n".join([
                "import threading, time",
                "from pathlib import Path",
                "from app.m2 import agent",
                f"agent.cleanup = lambda run_id: Path({str(cleaned)!r}).write_text(run_id)",
                f"command = ['bash', '-c', 'echo $$ > {pid_file}; exec sleep 60']",
                "env = {'PATH': '/usr/bin:/bin', 'HARNESS_RUN_ID': 'run-x'}",
                "threading.Thread(target=agent.run_group, args=(command, 60), kwargs={'env': env}, daemon=True).start()",
                f"while not (Path({str(pid_file)!r}).exists() and Path({str(pid_file)!r}).read_text().strip()):",
                "    time.sleep(0.05)",
            ])
            subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                           check=True, timeout=30)
            self.assertFalse(alive(int(pid_file.read_text())), "the agent outlived the Python process")
            self.assertEqual(cleaned.read_text(), "run-x")  # its sandbox containers were removed too

    def test_sandbox_script_applies_limits_label_and_no_host_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "docker"
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            fake.chmod(0o755)
            env = {"PATH": f"{tmp}:{os.environ['PATH']}", "SANDBOX_IMAGE": "img", "HARNESS_RUN_ID": "run-x",
                   "ANTHROPIC_API_KEY": "secret-key"}
            args = subprocess.run([agent.SANDBOX, "npm run build"], cwd=tmp, env=env, capture_output=True,
                                  text=True, check=True).stdout.splitlines()
            self.assertIn("\n".join(agent.LIMITS), "\n".join(args))  # same limits as the app server
            self.assertEqual(args[args.index("--label") + 1], "harness.run=run-x")
            self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == "-e"],
                             ["HOME=/tmp", "npm_config_cache=/tmp/.npm"])
            self.assertNotIn("secret-key", "\n".join(args))
            self.assertEqual(args[-7:], ["timeout", "-k", "10", "900", "bash", "-c", "npm run build"])
            del env["HARNESS_RUN_ID"]
            self.assertNotEqual(subprocess.run([agent.SANDBOX, "true"], cwd=tmp, env=env,
                                               capture_output=True).returncode, 0)

    def test_wait_up_tells_http_error_from_no_connection(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        class Failing(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(500)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Failing)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        running = {"Running": True, "ExitCode": 0, "OOMKilled": False, "Error": ""}
        with patch.object(verify, "container_state", return_value=running):
            self.assertEqual(verify.wait_up(f"http://127.0.0.1:{server.server_port}", "c", seconds=0),
                             {"status": 500, "error": "HTTP 500"})
            closed = verify.wait_up(f"http://127.0.0.1:{verify.free_port()}", "c", seconds=0)
        self.assertIsNone(closed["status"])
        self.assertIn("refused", closed["error"].lower())
        unknown = dict.fromkeys(running)  # docker inspect failed: not known to be stopped, keep waiting
        with patch.object(verify, "container_state", return_value=unknown):
            self.assertNotIn("container", verify.wait_up(f"http://127.0.0.1:{verify.free_port()}", "c", seconds=0))
        crashed = {**running, "Running": False, "ExitCode": 1}
        with patch.object(verify, "container_state", return_value=crashed):
            up = verify.wait_up(f"http://127.0.0.1:{verify.free_port()}", "c", seconds=60)  # no 60 s wait
        self.assertEqual(up["container"]["ExitCode"], 1)
        failures = verify.judge({"root_status": None, "root_wait": up, "root_status_after_restart": None,
                                 "checks": [], "checks_after_restart": []}, Spec.model_validate(spec_data()))["failures"]
        self.assertIn("container stopped", failures[0])

    def fake_docker(self, build=0, start=0, restart=0, docker=0, image=0):
        """Stands in for run_group: sandbox.sh calls and docker CLI calls answer as configured."""
        calls = []

        def run(command, timeout, **kwargs):
            calls.append(command)
            if command[0] == agent.SANDBOX:
                return (build, "", "build log") if command[1] == verify.BUILD else (0, "3221225472\n", "")
            verb = command[1]
            answers = {"version": (docker, "27.1.1\n", "Cannot connect to the Docker daemon"),
                       "image": (image, "sha256:abc\n", ""),
                       "run": (start, "", "port is already allocated" if start else "cid\n"),
                       "restart": (restart, "", ""),
                       "logs": (0, "GET / 200\n", "Traceback: boom\n"),
                       "inspect": (0, json.dumps({"Running": True, "Memory": 3221225472, "PidsLimit": 512}), "")}
            return answers.get(verb, (0, "", ""))
        return run, calls

    def verify_with(self, **docker):
        run_fn, calls = self.fake_docker(**docker)
        ok = {"status": 200, "error": None}
        with tempfile.TemporaryDirectory() as tmp, patch.object(verify, "run_group", run_fn), \
                patch.object(verify, "cleanup", return_value=[]), \
                patch.object(verify, "wait_up", return_value=ok), \
                patch.object(verify, "run_checks", side_effect=lambda base, checks: [
                    {"id": c.id, "passed": True} for c in checks if c.http_steps]):
            app = Path(tmp) / "run-t" / "app"
            (app / "data").mkdir(parents=True)
            (app / "data" / "app.db").write_text("rows the agent added while testing")
            result = verify.verify(app, Spec.model_validate(spec_data()), "harness-run-t")
            result["data_left"] = (app / "data").exists()
            kept = app.parent / "data-before-verify" / "app.db"
            result["kept_text"] = kept.read_text() if kept.exists() else None
            return result, calls

    def test_verify_passes_and_records_environment_and_limits(self):
        result, calls = self.verify_with()
        self.assertEqual(result["status"], "passed")
        self.assertIsNone(result["failure_stage"])
        self.assertEqual(result["environment"]["image_id"], "sha256:abc")
        self.assertEqual([s["phase"] for s in result["steps"]], ["limits", "build", "start", "restart"])
        self.assertTrue(all(isinstance(s["duration_s"], float) for s in result["steps"]))
        start = next(c for c in calls if c[:2] == ["docker", "run"])
        self.assertIn("\n".join(agent.LIMITS), "\n".join(start))
        self.assertEqual(result["server_limits"]["PidsLimit"], 512)
        self.assertIn("Traceback: boom", result["server_log_tail"])  # the server's stderr is kept
        self.assertFalse(result["data_left"])  # checks start from the seed, not the agent's test rows
        self.assertTrue(result["data_kept"].endswith("data-before-verify"))
        self.assertEqual(result["kept_text"], "rows the agent added while testing")
        self.assertIn(["docker", "rm", "-f", "harness-run-t"], calls)  # not kept without keep_running

    def test_verify_tells_each_failing_stage_apart(self):
        cases = {"build timeout": ({"build": 124}, "build_failed", "build", "timeout"),
                 "host timeout": ({"build": None}, "build_failed", "build", "timeout"),
                 "build error": ({"build": 1}, "build_failed", "build", "failed"),
                 "build killed": ({"build": 137}, "build_failed", "build", "killed"),
                 "start failure": ({"start": 125}, "start_failed", "start", "docker_error"),
                 "restart failure": ({"restart": 1}, "checks_failed", "restart", "failed")}
        for name, (docker, status, stage, outcome) in cases.items():
            with self.subTest(name):
                result, _ = self.verify_with(**docker)
                self.assertEqual((result["status"], result["failure_stage"]), (status, stage))
                self.assertEqual(result["steps"][-1]["phase"], stage)
                self.assertEqual(result["steps"][-1]["outcome"], outcome)
                self.assertTrue(result["failures"])

    def test_verify_stops_before_building_without_docker_or_image(self):
        for docker in ({"docker": 1}, {"image": 1}):
            with self.subTest(docker):
                result, calls = self.verify_with(**docker)
                self.assertEqual((result["status"], result["failure_stage"]), ("environment_error", "preflight"))
                self.assertFalse(any(c[0] == agent.SANDBOX for c in calls))

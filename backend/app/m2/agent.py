"""E5: Claude Code (headless, Sonnet) implements one task in the app workspace; the harness
commits the result. The workspace is a local Git repo outside the harness repo with no remote."""

import atexit
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app.llm import claude
from app.m2.stages import prompt

HARNESS_REPO = Path(__file__).resolve().parents[3]
SANDBOX = str(Path(__file__).resolve().parent / "sandbox" / "sandbox.sh")
SANDBOX_IMAGE = "harness-sandbox:m2-1"
# Resource and privilege limits of every sandbox container. sandbox.sh repeats them literally so
# the agent cannot loosen them through the environment; a test keeps the two in sync.
LIMITS = ("--memory", "3g", "--cpus", "2", "--pids-limit", "512",
          "--cap-drop", "ALL", "--security-opt", "no-new-privileges")
# Same exclusions as the stack contract's .gitignore, enforced even if the agent forgets it.
GIT_EXCLUDE = ".git\nnode_modules/\nfrontend/dist/\ndata/\n.venv/\n__pycache__/\n.env\n"
AGENT_TIMEOUT_S = 1800
DOCKER_TIMEOUT_S = 120  # one docker CLI call (ps, rm, inspect, run -d ...)
_running = {}  # process group id -> run id of its sandbox containers, while run_group waits for it


def run_group(command, timeout, **kwargs):
    """subprocess.run for commands that start children (Claude Code, sandbox.sh -> docker CLI):
    the command gets its own process group, and the whole group is killed on timeout or
    interrupt so no child outlives the call. Returns (exit code, stdout, stderr); the exit code
    is None on timeout."""
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True, **kwargs)
    _running[proc.pid] = (kwargs.get("env") or {}).get("HARNESS_RUN_ID")
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:  # a child that left the group still holds the pipes
            out, err = "", ""
        return None, out, err
    except BaseException:  # Ctrl+C: the new session no longer receives the terminal's SIGINT
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise
    finally:
        _running.pop(proc.pid, None)


def run_id_of(app_dir):
    # Workspace layout: <HARNESS_WORKSPACE_ROOT>/<run_id>/app. Resolved so `--resume .` still has a name.
    return Path(app_dir).resolve().parent.name


def sandbox_env(app_dir):
    """Variables sandbox.sh needs; they stay on the host and are not passed into the container."""
    return {"SANDBOX_IMAGE": SANDBOX_IMAGE, "HARNESS_RUN_ID": run_id_of(app_dir)}


class CleanupError(RuntimeError):
    """Docker could not list or remove a run's sandbox containers: whether any are left is unknown."""


def docker_call(what, command):
    """Run one bounded docker call (a hung Docker must not hang a task's end or Python's exit);
    return its stdout or raise CleanupError naming the call and why it failed."""
    try:
        code, out, err = run_group(command, DOCKER_TIMEOUT_S)
    except OSError as exc:  # no docker CLI
        raise CleanupError(f"{what} failed: {exc}") from exc
    if code != 0:
        raise CleanupError(f"{what} {'timed out' if code is None else f'failed (exit {code})'}: {err.strip()[-300:]}")
    return out


def cleanup(run_id):
    """Force-remove the sandbox.sh containers of a run (found by their label) and return their
    ids, so the record shows what a finished, timed-out or interrupted caller left behind. Raises
    CleanupError when they could not be listed or removed, never reporting "none left" instead."""
    ids = docker_call("docker ps", ["docker", "ps", "-aq", "--filter", f"label=harness.run={run_id}"]).split()
    if ids:
        docker_call(f"docker rm {' '.join(ids)}", ["docker", "rm", "-f", *ids])
    return ids


@atexit.register
def stop_running():
    """A caller that runs the agent in a thread (the web server) stops only its main thread on
    Ctrl+C, and the agent's own session never gets the terminal's SIGINT. When Python exits, end
    the groups still running and remove their runs' sandbox containers."""
    running = dict(_running)
    for pgid in running:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:  # the group already ended
            pass
    for run_id in set(filter(None, running.values())):
        try:
            cleanup(run_id)
        except CleanupError as exc:  # nothing more can be done while Python exits
            print(f"harness: {exc}", file=sys.stderr)


def git_dir(app_dir):
    return Path(app_dir).parent / "app.git"


def git(app_dir, *args):
    # The repository lives next to the work tree, not inside it: the sandbox mounts only the
    # work tree, so generated code can never plant hooks or config that the host git would run.
    return subprocess.run(["git", f"--git-dir={git_dir(app_dir)}", f"--work-tree={app_dir}", *args],
                          cwd=app_dir, capture_output=True, text=True, check=True).stdout


def init_workspace(app_dir):
    app_dir = Path(app_dir).resolve()
    if app_dir == HARNESS_REPO or HARNESS_REPO in app_dir.parents:
        raise ValueError("작업 폴더는 Harness 저장소 밖이어야 합니다.")
    app_dir.mkdir(parents=True, exist_ok=False)
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(git_dir(app_dir))], check=True)
    git(app_dir, "config", "core.bare", "false")
    git(app_dir, "config", "user.name", "harness-agent")
    git(app_dir, "config", "user.email", "harness-agent@localhost")
    (git_dir(app_dir) / "info/exclude").write_text(GIT_EXCLUDE)
    git(app_dir, "commit", "-q", "--allow-empty", "-m", "init: empty workspace")
    return app_dir


def assert_no_remote(app_dir):
    if git(app_dir, "remote").strip():
        raise RuntimeError("생성 작업 폴더에 원격 저장소가 연결되어 있습니다.")


def agent_command(task_prompt, budget_usd):
    return ["claude", "--bare", "-p", task_prompt,
            "--model", claude.model_for("sonnet"),
            "--append-system-prompt", prompt("coding_task").replace("{sandbox}", SANDBOX),
            "--permission-mode", "dontAsk",
            # File tools are scoped to the workspace (cwd). Glob/Grep are left out because they
            # can search outside it; the agent searches with the sandbox, which only mounts cwd.
            "--allowedTools", "Read(./**)", "Edit(./**)", "Write(./**)", f"Bash({SANDBOX}:*)",
            "--output-format", "json", "--max-budget-usd", str(budget_usd),
            "--no-session-persistence"]


def agent_env(app_dir):
    # Drop variables of any Claude Code session that launched the harness; --bare then
    # authenticates strictly with ANTHROPIC_API_KEY (API billing, not a subscription login).
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")}
    return {**env, "DISABLE_AUTOUPDATER": "1", **sandbox_env(app_dir)}


def run_task(task, context, app_dir, budget_usd, runner=run_group):
    """Run the agent, then commit whatever it changed. Returns a record; never raises on agent
    failure so the caller can log it and stop. Containers left by an earlier attempt are removed
    first, and CleanupError is raised before the agent starts if that fails. A cleanup that fails
    after the agent ran is kept as `cleanup_error`, next to the agent's result and cost."""
    assert_no_remote(app_dir)
    run_id = run_id_of(app_dir)
    leftover = cleanup(run_id)  # an attempt that was killed, or whose own cleanup failed
    before = git(app_dir, "rev-parse", "HEAD").strip()
    command = agent_command(json.dumps(context, ensure_ascii=False), budget_usd)
    record = {"task_id": task.id, "title": task.title, "base_commit": before,
              "command": command[:3] + ["<context json>"] + command[4:], "leftover_containers": leftover}
    record["started_at"], started = claude.now(), time.monotonic()
    try:
        code, out, err = runner(command, AGENT_TIMEOUT_S, cwd=app_dir, env=agent_env(app_dir))
    finally:
        record["ended_at"], record["duration_s"] = claude.now(), round(time.monotonic() - started, 1)
        # Sandbox containers the agent started, background ones included, end with its task.
        try:
            record["leftover_containers"] += cleanup(run_id)
        except CleanupError as exc:
            record["cleanup_error"] = str(exc)
    record["exit_code"], record["stderr_tail"] = code, err[-2000:]
    if code is None:
        result = {"is_error": True, "result": "agent timeout"}
    else:
        try:
            result = json.loads(out)
        except ValueError:
            result = {"is_error": True, "result": out[-2000:]}
    record["agent"] = {k: result.get(k) for k in
                       ("is_error", "subtype", "result", "num_turns", "total_cost_usd", "usage", "modelUsage")}
    # Claude Code reports its own total (nested calls included); an agent that never finished reports
    # nothing, and that cost stays unknown, never 0 (#12).
    if code is None:
        record.update(cost_status="unknown", unknown_reason="agent_timeout")
    elif record["agent"]["total_cost_usd"] is None:
        record.update(cost_status="unknown", unknown_reason="cost_not_reported")
    else:
        record.update(cost_status="reported", unknown_reason=None)
    git(app_dir, "add", "-A")
    changed = git(app_dir, "status", "--porcelain").strip()
    if changed:
        status = "failed" if result.get("is_error") or record["exit_code"] != 0 else "done"
        git(app_dir, "commit", "-q", "-m", f"{task.id}: {task.title} [{status}]")
    record["commit"] = git(app_dir, "rev-parse", "HEAD").strip()
    record["files"] = git(app_dir, "diff", "--name-status", before, record["commit"]).splitlines()
    record["diff_stat"] = git(app_dir, "diff", "--stat", before, record["commit"]).strip()
    record["status"] = "failed" if result.get("is_error") or record["exit_code"] != 0 or not changed else "done"
    assert_no_remote(app_dir)
    return record

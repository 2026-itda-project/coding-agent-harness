"""E6: build and run the generated app in the Docker sandbox, then run the spec's HTTP checks,
restart, and rerun the persistence checks. Pass/fail comes from real responses only. Every
sandbox and docker step is recorded with its outcome, exit code and duration, also on failure."""

import json
import os
import shutil
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.m2.agent import (DOCKER_TIMEOUT_S, LIMITS, SANDBOX, SANDBOX_IMAGE as IMAGE, cleanup, run_group,
                          run_id_of, sandbox_env)
BUILD = ("cd frontend && npm install && npm run build && cd .. && python3 -m venv .venv"
         " && .venv/bin/pip install -q -r backend/requirements.txt")
SERVE = ".venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000"
# Limits as the container sees them (cgroup v2), so the record shows they were applied.
LIMITS_PROBE = "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.max /sys/fs/cgroup/pids.max"
# Exit codes: `timeout` in sandbox.sh, `docker run`, and SIGKILL (a command that ignored TERM and was
# killed by `timeout -k`, or the out-of-memory killer; the two give the same code).
TIMED_OUT, DOCKER_ERROR, KILLED = 124, 125, 137


def step(record, phase, command, timeout, **kwargs):
    """Run one command, append its record to record["steps"] and return the outcome."""
    started = time.monotonic()
    code, out, err = run_group(command, timeout, **kwargs)
    outcome = ("ok" if code == 0 else "timeout" if code in (None, TIMED_OUT)
               else "docker_error" if code == DOCKER_ERROR else "killed" if code == KILLED else "failed")
    record["steps"].append({"phase": phase, "outcome": outcome, "exit_code": code,
                            "duration_s": round(time.monotonic() - started, 1),
                            "output_tail": (out + err)[-4000:]})
    return outcome


def docker_json(*args):
    code, out, _ = run_group(["docker", *args], DOCKER_TIMEOUT_S)
    return json.loads(out) if code == 0 else None


def environment():
    """Docker server version and sandbox image id; `error` when either is unavailable."""
    try:
        code, out, err = run_group(["docker", "version", "-f", "{{.Server.Version}}"], DOCKER_TIMEOUT_S)
    except OSError as exc:  # no docker CLI at all
        return {"error": f"docker unavailable: {exc}"}
    if code != 0:
        return {"error": f"docker unavailable: {(out + err).strip()[-500:]}"}
    env = {"docker_server": out.strip(), "image": IMAGE, "limits": list(LIMITS),
           "sandbox_timeout_s": int(os.environ.get("SANDBOX_TIMEOUT", "900"))}
    code, out, _ = run_group(["docker", "image", "inspect", "-f", "{{.Id}}", IMAGE], DOCKER_TIMEOUT_S)
    if code != 0:
        return {**env, "error": f"image {IMAGE} is missing; build it with docker build -t {IMAGE} app/m2/sandbox"}
    return {**env, "image_id": out.strip()}


def run_step(base, step):
    data = step.body_json.encode() if step.body_json else None
    headers = {"Content-Type": "application/json", **({"X-Role": step.role_header} if step.role_header else {})}
    req = Request(base + step.path, data=data, method=step.method, headers=headers)
    try:
        with urlopen(req, timeout=15) as resp:
            status, body = resp.status, resp.read().decode(errors="replace")
    except HTTPError as exc:
        status, body = exc.code, exc.read().decode(errors="replace")
    except (URLError, OSError) as exc:
        status, body = None, str(exc)
    missing = [s for s in step.expect_contains if s not in body]
    return {"method": step.method, "path": step.path, "status": status, "expect_status": step.expect_status,
            "missing": missing, "passed": status == step.expect_status and not missing, "body_head": body[:300]}


def run_checks(base, checks):
    results = []
    for check in checks:
        if not check.http_steps:
            continue
        steps = [run_step(base, s) for s in check.http_steps]
        results.append({"id": check.id, "ac_ids": check.ac_ids, "steps": steps,
                        "passed": bool(steps) and all(s["passed"] for s in steps),
                        "manual_steps": check.manual_steps})
    return results


def container_state(name):
    state = docker_json("inspect", "-f", "{{json .State}}", name) or {}
    return {k: state.get(k) for k in ("Running", "ExitCode", "OOMKilled", "Error")}


def wait_up(base, name, seconds=90):
    """GET / until it answers 200 or time runs out, and return the last answer: an HTTP status
    (an error status means the server is up but failing) or None with the connection error.
    Stops early with the container state when the container is no longer running."""
    deadline = time.time() + seconds
    while True:
        try:
            with urlopen(base + "/", timeout=5) as resp:
                return {"status": resp.status, "error": None}
        except HTTPError as exc:  # an HTTPError is also a URLError: keep its status
            last = {"status": exc.code, "error": f"HTTP {exc.code}"}
            exc.close()
        except (URLError, OSError) as exc:
            last = {"status": None, "error": str(getattr(exc, "reason", exc))}
        state = container_state(name)
        if state["Running"] is False:  # None: docker inspect failed, so keep waiting
            return {**last, "container": state}
        if time.time() >= deadline:
            return last
        time.sleep(2)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def verify(app_dir, spec, name, keep_running=False):
    record = {"steps": [], "checks": [], "checks_after_restart": [],
              "root_status": None, "root_status_after_restart": None, "environment": environment()}
    if "error" in record["environment"]:
        return stopped(record, "environment_error", "preflight", record["environment"]["error"])
    env = {**os.environ, **sandbox_env(app_dir)}
    run_id = run_id_of(app_dir)
    record["leftover_containers"] = cleanup(run_id)  # from an earlier, interrupted attempt
    run_group(["docker", "rm", "-f", name], DOCKER_TIMEOUT_S)
    # Runtime data (data/app.db in the stack contract) left by the agent's own testing or an earlier
    # verification changes what the checks see, so every verification starts from the seed data.
    # The data found is kept next to the work tree (not mounted, not in Git); only the latest.
    data, kept = Path(app_dir) / "data", Path(app_dir).parent / "data-before-verify"
    record["data_kept"] = str(kept) if data.exists() else None
    if data.exists():
        shutil.rmtree(kept, ignore_errors=True)
        shutil.move(data, kept)
    try:
        step(record, "limits", [SANDBOX, LIMITS_PROBE], DOCKER_TIMEOUT_S, cwd=app_dir, env=env)
        # The host timeout only backs up the container's own `timeout`, so it is a bit longer.
        if step(record, "build", [SANDBOX, BUILD], record["environment"]["sandbox_timeout_s"] + 60,
                cwd=app_dir, env=env) != "ok":
            return stopped(record, "build_failed", "build")
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        uid = f"{os.getuid()}:{os.getgid()}"
        if step(record, "start", ["docker", "run", "-d", "--name", name, "--user", uid, "-e", "HOME=/tmp",
                                  *LIMITS, "-v", f"{app_dir}:/work", "-w", "/work",
                                  "-p", f"127.0.0.1:{port}:8000", IMAGE, "bash", "-c", SERVE],
                DOCKER_TIMEOUT_S) != "ok":
            return stopped(record, "start_failed", "start")
        host = docker_json("inspect", "-f", "{{json .HostConfig}}", name) or {}
        record["server_limits"] = {k: host.get(k) for k in ("Memory", "NanoCpus", "PidsLimit", "CapDrop", "SecurityOpt")}
        up = wait_up(base, name)
        record["root_status"], record["root_wait"] = up["status"], up
        record["checks"] = run_checks(base, spec.checks) if up["status"] == 200 else []
        if step(record, "restart", ["docker", "restart", name], DOCKER_TIMEOUT_S) == "ok":
            up = wait_up(base, name)
            record["root_status_after_restart"], record["root_wait_after_restart"] = up["status"], up
            persist = [c for c in spec.checks if c.rerun_after_restart]
            record["checks_after_restart"] = run_checks(base, persist) if up["status"] == 200 else []
        # docker logs replays the server's stderr (uvicorn startup, tracebacks) on its own stderr.
        _, out, err = run_group(["docker", "logs", "--tail", "80", name], DOCKER_TIMEOUT_S)
        record["server_log_tail"] = (out + err)[-4000:]
    finally:
        record["leftover_containers"] += cleanup(run_id)
        if not keep_running or record.get("status") == "start_failed":
            run_group(["docker", "rm", "-f", name], DOCKER_TIMEOUT_S)
    record["preview_url"] = base if keep_running else None
    return judge(record, spec)


def stopped(record, status, stage, reason=None):
    """A verification that ended before the HTTP checks; the reason is the last step's outcome."""
    last = record["steps"][-1] if record["steps"] else {}
    reason = reason or f"{stage}: {last.get('outcome')} (exit {last.get('exit_code')})"
    record.update(status=status, failure_stage=stage, failures=[reason])
    return record


def describe(wait, status):
    text = f"GET / -> {status}"
    if wait and wait.get("error"):
        text += f" ({wait['error']})"
    if wait and "container" in wait:
        text += f"; container stopped: {wait['container']}"
    return text


def judge(record, spec):
    """Pass only if both starts answered and every planned check ran and passed; a check that
    never ran is a failure, not a missing number."""
    automatic = [c for c in spec.checks if c.http_steps]
    planned = len(automatic) + sum(c.rerun_after_restart for c in automatic)
    manual = [{"id": c.id, "ac_ids": c.ac_ids, "steps": c.manual_steps,
               "rerun_after_restart": c.rerun_after_restart, "status": "pending"}
              for c in spec.checks if c.manual_steps]
    ran = record["checks"] + record["checks_after_restart"]
    restart = next((s for s in record.get("steps", []) if s["phase"] == "restart"), None)
    failures = []
    if record["root_status"] != 200:
        failures.append(f"app did not start: {describe(record.get('root_wait'), record['root_status'])}")
    if restart and restart["outcome"] != "ok":
        failures.append(f"restart failed: docker restart {restart['outcome']} (exit {restart['exit_code']})")
    elif record["root_status_after_restart"] != 200:
        failures.append("app did not come back after restart: "
                        + describe(record.get("root_wait_after_restart"), record["root_status_after_restart"]))
    if len(ran) < planned:
        failures.append(f"{planned - len(ran)} planned checks did not run")
    failures += [f"{c['id']} failed" for c in ran if not c["passed"]]
    automatic_status = "failed" if failures else "passed" if planned else "not_applicable"
    stage = None
    if failures:
        stage = ("serve" if record["root_status"] != 200
                 else "restart" if (restart and restart["outcome"] != "ok") or record["root_status_after_restart"] != 200
                 else "checks")
    record.update(passed=sum(c["passed"] for c in ran), total=planned, failures=failures,
                  automatic_status=automatic_status, manual_checks=manual, failure_stage=stage,
                  status="checks_failed" if failures or not (planned or manual)
                  else "manual_pending" if manual else "passed")
    return record

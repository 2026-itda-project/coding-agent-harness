"""E6: build and run the generated app in the Docker sandbox, then run the spec's HTTP checks,
restart, and rerun the persistence checks. Pass/fail comes from real responses only."""

import os
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.m2.agent import SANDBOX, SANDBOX_IMAGE as IMAGE
BUILD = ("cd frontend && npm install && npm run build && cd .. && python3 -m venv .venv"
         " && .venv/bin/pip install -q -r backend/requirements.txt")
SERVE = ".venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000"


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
        steps = [run_step(base, s) for s in check.http_steps]
        results.append({"id": check.id, "ac_ids": check.ac_ids, "steps": steps,
                        "passed": bool(steps) and all(s["passed"] for s in steps),
                        "manual_steps": check.manual_steps})
    return results


def wait_up(base, seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urlopen(base + "/", timeout=5) as resp:
                return resp.status
        except (URLError, OSError):
            time.sleep(2)
    return None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def verify(app_dir, spec, name, keep_running=False):
    record = {"image": IMAGE}
    build = subprocess.run([SANDBOX, BUILD], cwd=app_dir, capture_output=True, text=True, timeout=1800,
                           env={**os.environ, "SANDBOX_IMAGE": IMAGE})
    record["build"] = {"exit_code": build.returncode, "log_tail": (build.stdout + build.stderr)[-4000:]}
    if build.returncode != 0:
        record["status"] = "build_failed"
        return record
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    uid = f"{os.getuid()}:{os.getgid()}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "--user", uid, "-e", "HOME=/tmp",
                    "-v", f"{app_dir}:/work", "-w", "/work", "-p", f"127.0.0.1:{port}:8000",
                    IMAGE, "bash", "-c", SERVE], check=True, capture_output=True)
    try:
        record["root_status"] = wait_up(base)
        record["checks"] = run_checks(base, spec.checks) if record["root_status"] == 200 else []
        subprocess.run(["docker", "restart", name], check=True, capture_output=True)
        record["root_status_after_restart"] = wait_up(base)
        persist = [c for c in spec.checks if c.rerun_after_restart]
        record["checks_after_restart"] = run_checks(base, persist) if record["root_status_after_restart"] == 200 else []
        record["server_log_tail"] = subprocess.run(["docker", "logs", "--tail", "80", name],
                                                   capture_output=True, text=True).stdout[-4000:]
    finally:
        if not keep_running:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    record["preview_url"] = base if keep_running else None
    all_checks = record["checks"] + record["checks_after_restart"]
    record["passed"] = sum(c["passed"] for c in all_checks)
    record["total"] = len(all_checks)
    record["status"] = ("passed" if record["root_status"] == 200 and all_checks and record["passed"] == record["total"]
                        else "checks_failed")
    return record

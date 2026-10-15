"""E4: the minimal context one coding task needs, with the source of every part."""

from pathlib import Path

from app.m2.agent import git
from app.m2.contracts import prd_items, spec_items
from app.m2.stages import prompt

MAX_FILE_CHARS = 20_000
MAX_TOTAL_CHARS = 120_000
SKIP_PARTS = {"node_modules", "dist", "data", ".venv", "__pycache__", ".git"}
SKIP_NAMES = {".env", "package-lock.json"}


def tracked_files(app_dir):
    return [f for f in git(app_dir, "ls-files").splitlines()
            if not SKIP_PARTS & set(Path(f).parts) and Path(f).name not in SKIP_NAMES]


def outside_reason(app_dir, name):
    """A symlink or a path resolving outside the work tree could pull host files (e.g. keys)
    into the model input, so such files are never read."""
    path = Path(app_dir) / name
    if path.is_symlink():
        return "symlink"
    if not path.resolve().is_relative_to(Path(app_dir).resolve()):
        return "outside_workspace"
    return None


def build_context(task, prd, spec, app_dir, results, sandbox, last_error=None):
    specs = spec_items(spec)
    items = prd_items(prd)
    task_specs = {sid: specs[sid].model_dump() for sid in task.spec_ids}
    checks = {c.id: c.model_dump() for c in spec.checks if c.id in task.check_ids}
    req_ids = sorted({r for s in task_specs.values() for r in s.get("requirement_ids", [])}
                     | {a for c in checks.values() for a in c["ac_ids"]})
    requirements = {r: items[r].model_dump() for r in req_ids if r in items}
    criteria = [ac.model_dump() for ac in prd.acceptance_criteria
                if ac.id in req_ids or set(ac.target_ids) & set(requirements)]
    files = tracked_files(app_dir)
    contents, skipped, total = {}, {}, 0
    # The task's own expected files first, then everything else, until the size cap.
    for name in [f for f in task.files if f in files] + [f for f in files if f not in task.files]:
        if reason := outside_reason(app_dir, name):
            skipped[name] = reason
            continue
        text = (Path(app_dir) / name).read_text(errors="replace")[:MAX_FILE_CHARS]
        if total + len(text) > MAX_TOTAL_CHARS:
            break
        contents[name], total = text, total + len(text)
    head = git(app_dir, "rev-parse", "HEAD").strip()
    return {
        "task": task.model_dump(),
        "spec_items": task_specs,
        "checks": checks,
        "requirements": requirements,
        "acceptance_criteria": criteria,
        "previous_tasks": [results[d] for d in task.depends_on if d in results],
        "repository": {"files": files, "contents": contents, "skipped": skipped,
                       "omitted": [f for f in files if f not in contents and f not in skipped]},
        "last_error": last_error,
        "environment": {"sandbox": sandbox, "stack_contract": prompt("stack_contract")},
        "sources": {
            "task": f"plan.json#{task.id}",
            "spec_items": [f"spec.json#{s}" for s in task_specs] + [f"spec.json#{c}" for c in checks],
            "requirements": [f"prd.json#{r}" for r in requirements] + [f"prd.json#{c['id']}" for c in criteria],
            "previous_tasks": [f"agent/{d}-attempt-{results[d].get('attempt', 1)}.json"
                               for d in task.depends_on if d in results],
            "repository": f"app@{head}",
        },
    }

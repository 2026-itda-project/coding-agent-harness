"""M2 PRD / Spec / Plan contracts and structural checks. Domain-agnostic: the code checks
shape and references only; product content comes from the LLM."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# ---------- PRD (E1) ----------

class PRDItem(Strict):
    id: str
    description: str
    status: Literal["confirmed", "proposed", "needs_clarification"]
    source: Literal["initial_input", "ai_proposal", "harness_policy"]


class AcceptanceCriterion(Strict):
    id: str
    description: str
    target_ids: list[str]


class Overview(Strict):
    summary: str
    problem: str
    goals: list[str]


class PRD(Strict):
    title: str
    overview: Overview
    in_scope: list[PRDItem]
    out_of_scope: list[PRDItem]
    roles: list[PRDItem]
    user_flows: list[PRDItem]
    functional_requirements: list[PRDItem]
    business_rules: list[PRDItem]
    nfrs: list[PRDItem]
    acceptance_criteria: list[AcceptanceCriterion]
    open_issues: list[PRDItem]


PRD_PREFIX = {"in_scope": "SCOPE-", "out_of_scope": "OUT-", "roles": "ROLE-", "user_flows": "US-",
              "functional_requirements": "FR-", "business_rules": "RULE-", "nfrs": "NFR-",
              "open_issues": "ISSUE-"}


def prd_items(prd):
    return {item.id: item for section in PRD_PREFIX for item in getattr(prd, section)}


def check_prd(prd):
    errors = []
    seen = set()
    for section, prefix in PRD_PREFIX.items():
        for item in getattr(prd, section):
            if item.id in seen:
                errors.append(f"duplicate id {item.id}")
            seen.add(item.id)
            if not item.id.startswith(prefix):
                errors.append(f"{item.id} must start with {prefix} in {section}")
            if not item.description.strip():
                errors.append(f"{item.id} empty description")
            if (item.status == "proposed") != (item.source == "ai_proposal"):
                errors.append(f"{item.id}: proposed status and ai_proposal source must go together")
            if item.source == "harness_policy" and item.status != "confirmed":
                errors.append(f"{item.id}: harness_policy items are confirmed platform constraints")
            if (item.status == "needs_clarification") != (section == "open_issues"):
                errors.append(f"{item.id}: needs_clarification belongs only in open_issues")
    items = prd_items(prd)
    for ac in prd.acceptance_criteria:
        if ac.id in seen or not ac.id.startswith("AC-"):
            errors.append(f"bad or duplicate AC id {ac.id}")
        seen.add(ac.id)
        missing = [t for t in ac.target_ids if t not in items]
        if not ac.target_ids or missing:
            errors.append(f"{ac.id} targets unknown ids {missing or 'none'}")
    covered = {t for ac in prd.acceptance_criteria for t in ac.target_ids}
    errors += [f"{fr.id} has no acceptance criterion" for fr in prd.functional_requirements if fr.id not in covered]
    for name in ("roles", "user_flows", "functional_requirements"):
        if not getattr(prd, name):
            errors.append(f"{name} is empty")
    return errors


# ---------- Spec (E2) ----------

class Field(Strict):
    name: str
    type: str
    description: str


class Screen(Strict):
    id: str
    name: str
    purpose: str
    role_ids: list[str]
    elements: list[str]
    requirement_ids: list[str]


class DataModel(Strict):
    id: str
    name: str
    fields: list[Field]
    seed_rows: int
    requirement_ids: list[str]


class Api(Strict):
    id: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    description: str
    request: str
    response: str
    requirement_ids: list[str]


class Permission(Strict):
    id: str
    role_id: str
    allowed: list[str]
    requirement_ids: list[str]


class RuleImpl(Strict):
    id: str
    description: str
    where: list[str]
    requirement_ids: list[str]


class MockIntegration(Strict):
    id: str
    description: str
    requirement_ids: list[str]


class HttpStep(Strict):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    role_header: str
    body_json: str
    expect_status: int
    expect_contains: list[str]


class Check(Strict):
    id: str
    ac_ids: list[str]
    description: str
    http_steps: list[HttpStep]
    rerun_after_restart: bool
    manual_steps: list[str]


class Spec(Strict):
    screens: list[Screen]
    data_models: list[DataModel]
    apis: list[Api]
    permissions: list[Permission]
    rule_implementations: list[RuleImpl]
    mock_integrations: list[MockIntegration]
    checks: list[Check]


SPEC_PREFIX = {"screens": "SCR-", "data_models": "DATA-", "apis": "API-", "permissions": "AUTH-",
               "rule_implementations": "IMPL-", "mock_integrations": "MOCK-", "checks": "CHK-"}


def spec_items(spec):
    return {item.id: item for section in SPEC_PREFIX for item in getattr(spec, section)}


def check_spec(spec, prd):
    errors = []
    prd_ids = set(prd_items(prd))
    ac_ids = {ac.id for ac in prd.acceptance_criteria}
    seen = set()
    for section, prefix in SPEC_PREFIX.items():
        for item in getattr(spec, section):
            if item.id in seen or not item.id.startswith(prefix):
                errors.append(f"bad or duplicate id {item.id} in {section}")
            seen.add(item.id)
            refs = getattr(item, "requirement_ids", None)
            if refs is not None:
                unknown = [r for r in refs if r not in prd_ids]
                if not refs or unknown:
                    errors.append(f"{item.id} requirement_ids invalid {unknown or 'empty'}")
    for screen in spec.screens:
        errors += [f"{screen.id} unknown role {r}" for r in screen.role_ids
                   if r not in prd_ids or not r.startswith("ROLE-")]
    for perm in spec.permissions:
        if perm.role_id not in prd_ids or not perm.role_id.startswith("ROLE-"):
            errors.append(f"{perm.id} unknown role {perm.role_id}")
    for impl in spec.rule_implementations:
        errors += [f"{impl.id} unknown location {w}" for w in impl.where if w not in seen]
    for api in spec.apis:
        if not api.path.startswith("/api/"):
            errors.append(f"{api.id} path must start with /api/")
    for check in spec.checks:
        unknown = [a for a in check.ac_ids if a not in ac_ids]
        if not check.ac_ids or unknown:
            errors.append(f"{check.id} ac_ids invalid {unknown or 'empty'}")
        if not check.http_steps and not check.manual_steps:
            errors.append(f"{check.id} has no steps")
    covered = {a for c in spec.checks for a in c.ac_ids}
    errors += [f"{ac} not covered by any check" for ac in sorted(ac_ids - covered)]
    referenced = {r for s in SPEC_PREFIX for i in getattr(spec, s) for r in getattr(i, "requirement_ids", [])}
    errors += [f"{fr.id} not referenced by spec" for fr in prd.functional_requirements if fr.id not in referenced]
    return errors


# ---------- Plan (E3) ----------

class Feature(Strict):
    id: str
    name: str
    spec_ids: list[str]


class Task(Strict):
    id: str
    feature_id: str
    title: str
    instructions: str
    spec_ids: list[str]
    depends_on: list[str]
    files: list[str]
    acceptance: list[str]
    check_ids: list[str]


class Plan(Strict):
    features: list[Feature]
    tasks: list[Task]


def order_tasks(plan):
    """Dependency order (stable by listed order). Raises ValueError on a cycle."""
    pending = {t.id: set(t.depends_on) for t in plan.tasks}
    order = []
    while pending:
        ready = [t.id for t in plan.tasks if t.id in pending and not pending[t.id] - set(order)]
        if not ready:
            raise ValueError(f"dependency cycle among {sorted(pending)}")
        order.append(ready[0])
        del pending[ready[0]]
    return order


def check_plan(plan, spec):
    errors = []
    sids = set(spec_items(spec))
    check_ids = {c.id for c in spec.checks}
    fids = [f.id for f in plan.features]
    tids = [t.id for t in plan.tasks]
    for ids, prefix in ((fids, "FEAT-"), (tids, "TASK-")):
        errors += [f"bad or duplicate id {i}" for i in ids if ids.count(i) > 1 or not i.startswith(prefix)]
    for feat in plan.features:
        errors += [f"{feat.id} unknown spec id {s}" for s in feat.spec_ids if s not in sids]
    for task in plan.tasks:
        if task.feature_id not in fids:
            errors.append(f"{task.id} unknown feature {task.feature_id}")
        errors += [f"{task.id} unknown spec id {s}" for s in task.spec_ids if s not in sids]
        errors += [f"{task.id} unknown dependency {d}" for d in task.depends_on if d not in tids]
        errors += [f"{task.id} unknown check {c}" for c in task.check_ids if c not in check_ids]
        if not task.instructions.strip():
            errors.append(f"{task.id} empty instructions")
    covered = {c for t in plan.tasks for c in t.check_ids}
    errors += [f"{c} not assigned to any task" for c in sorted(check_ids - covered)]
    if not errors:
        try:
            order_tasks(plan)
        except ValueError as exc:
            errors.append(str(exc))
    return errors


# ---------- JSON schema for structured outputs ----------

def output_schema(model):
    """Pydantic schema with $refs inlined and titles dropped, for output_config.format."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node, is_properties=False):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(defs[node["$ref"].split("/")[-1]])
            # Drop pydantic's "title" metadata, but keep properties that are named "title".
            return {k: inline(v, k == "properties") for k, v in node.items() if is_properties or k != "title"}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    return inline(schema)

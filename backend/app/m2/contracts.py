"""M2 PRD / Spec / Plan contracts and structural checks. Domain-agnostic: the code checks
shape and references only; product content comes from the LLM."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# ---------- PRD (E1) ----------

REVIEW_SOURCES = ("clarification_answer", "review_input")


class PRDItem(Strict):
    id: str
    description: str
    status: Literal["confirmed", "proposed", "needs_clarification"]
    source: Literal["initial_input", "ai_proposal", "harness_policy", "clarification_answer", "review_input"]
    # Review handoff links (Requirement / Gap ids). Empty for a PRD generated straight from input,
    # so fixed PRD fixtures without these fields stay valid.
    requirement_ids: list[str] = []
    gap_ids: list[str] = []


class ReviewedPRDItem(PRDItem):
    """PRD item generated from a Review handoff: the links are required in the model output."""
    requirement_ids: list[str]
    gap_ids: list[str]


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


class ReviewedPRD(PRD):
    in_scope: list[ReviewedPRDItem]
    out_of_scope: list[ReviewedPRDItem]
    roles: list[ReviewedPRDItem]
    user_flows: list[ReviewedPRDItem]
    functional_requirements: list[ReviewedPRDItem]
    business_rules: list[ReviewedPRDItem]
    nfrs: list[ReviewedPRDItem]
    open_issues: list[ReviewedPRDItem]


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
            # ai_proposal may be proposed (not yet accepted), confirmed (accepted in Review) or an
            # open needs_clarification; proposed content is always an AI proposal.
            if item.status == "proposed" and item.source != "ai_proposal":
                errors.append(f"{item.id}: proposed status requires ai_proposal source")
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


def check_generated_prd(prd):
    """A PRD generated straight from user input had no Review, so no AI proposal can be accepted yet."""
    items = prd_items(prd).values()
    return (check_prd(prd)
            + [f"{i.id}: confirmed ai_proposal without user acceptance"
               for i in items if i.status == "confirmed" and i.source == "ai_proposal"]
            + [f"{i.id}: {i.source} needs a Review handoff" for i in items if i.source in REVIEW_SOURCES]
            + [f"{i.id}: Review links without a Review handoff" for i in items if i.requirement_ids or i.gap_ids])


def check_reviewed_prd(prd, reviewed):
    """A PRD generated from an approved Review handoff (`schemas.ReviewedRequirements`). Statuses and
    sources must be backed by the handoff: the code checks the links, never rewrites content."""
    errors = check_prd(prd)
    reqs = {r.id: r for r in reviewed.requirements}
    open_gap_ids = {g.id for g in reviewed.open_gaps}
    linked = {l.gap.id: l.requirement_id for l in reviewed.gap_links}
    covered_reqs, gap_items = set(), {}
    for section in PRD_PREFIX:
        for item in getattr(prd, section):
            refs = [reqs.get(r) for r in item.requirement_ids]
            if None in refs:
                errors.append(f"{item.id} references unknown requirement ids")
                continue
            covered_reqs.update(item.requirement_ids)
            for gid in item.gap_ids:
                if gid not in open_gap_ids and gid not in linked:
                    errors.append(f"{item.id} references unknown gap {gid}")
                gap_items.setdefault(gid, []).append((section, item))
            if item.source in ("initial_input", *REVIEW_SOURCES) or (
                    item.source == "ai_proposal" and item.status == "confirmed"):
                # user content and accepted proposals must come from handoff requirements of the same kind
                if not refs or any(r.source != item.source or r.status != item.status for r in refs):
                    errors.append(f"{item.id}: {item.status} {item.source} is not backed by a handoff "
                                  "requirement with the same status and source")
            elif item.status == "proposed" and any(r.status == "confirmed" and r.source == "ai_proposal"
                                                   for r in refs):
                errors.append(f"{item.id}: an accepted proposal must stay confirmed")
    errors += [f"{rid} from the Review handoff is missing in the PRD" for rid in sorted(set(reqs) - covered_reqs)]
    open_issue_reqs = {r for i in prd.open_issues for r in i.requirement_ids}
    errors += [f"{r.id} is undecided but not in open_issues" for r in reviewed.requirements
               if r.status == "needs_clarification" and r.id not in open_issue_reqs]
    errors += [f"{gid} (open gap) is missing in the PRD" for gid in sorted(open_gap_ids - set(gap_items))]
    for gid, req_id in linked.items():
        if not gap_items.get(gid):
            errors.append(f"{gid} (linked to {req_id} in Review) is missing in the PRD")
        for section, item in gap_items.get(gid, []):
            # a gap linked to an undecided requirement stays open together with it
            reopened = section == "open_issues" and (req_id not in reqs or reqs[req_id].status != "needs_clarification")
            if reopened or req_id not in item.requirement_ids:
                errors.append(f"{gid} is linked to {req_id} in Review; {item.id} must not reopen or relink it")
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
        for step in check.http_steps:
            if not step.path.startswith("/"):
                errors.append(f"{check.id} HTTP step path must start with /")
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


# ---------- Manual review (E6 acceptance) ----------

class ManualReviewEntry(Strict):
    """One person's verdict on one planned manual step. `step` repeats the Spec text and step_index
    identifies it, as a check may list the same text twice."""
    check_id: str
    step_index: int
    step: str
    verdict: Literal["pass", "fail"]
    evidence: str


class ManualReviewSubmission(Strict):
    """What a person sends. The server adds the time and the code the verdicts apply to."""
    reviewer: str
    items: list[ManualReviewEntry]


class ManualReviewItem(ManualReviewEntry):
    reviewer: str
    reviewed_at: str
    code_commit: str
    spec_sha256: str


class ManualReview(Strict):
    schema_version: Literal[1]
    reviewer: str  # the latest submission; submissions may be partial, so each item keeps its own
    reviewed_at: str
    items: list[ManualReviewItem]


def check_manual_entries(reviewer, items, spec):
    errors = [] if reviewer.strip() else ["reviewer is empty"]
    checks = {c.id: c for c in spec.checks}
    seen = set()
    for entry in items:
        where = f"{entry.check_id} step {entry.step_index}"
        check = checks.get(entry.check_id)
        if check is None:
            errors.append(f"{entry.check_id} unknown check")
            continue
        if (entry.check_id, entry.step_index) in seen:
            errors.append(f"{where} duplicate")
        seen.add((entry.check_id, entry.step_index))
        if not 0 <= entry.step_index < len(check.manual_steps):
            errors.append(f"{where} is not a planned manual step")
        elif check.manual_steps[entry.step_index] != entry.step:
            errors.append(f"{where} text differs from the spec")
        if not entry.evidence.strip():
            errors.append(f"{where} has no evidence")
    return errors


def check_manual_submission(submission, spec):
    return check_manual_entries(submission.reviewer, submission.items, spec) + (
        [] if submission.items else ["items is empty"])


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

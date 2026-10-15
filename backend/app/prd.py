"""PRD 생성·검증. ReviewedRequirements를 받아 PRD로 조립한다."""
from app.schemas import PRD, AcceptanceCriterion, PRDItem, ProductOverview, Requirement, ReviewedRequirements

SECTION_BY_CATEGORY = {
    "role": "roles", "flow": "user_flows", "functional": "functional_requirements",
    "permission": "business_rules", "business_rule": "business_rules",
    "state": "business_rules", "exception": "business_rules",
    "nfr": "nfrs", "scope": "in_scope", "integration": "in_scope",
}

PREFIX_BY_SECTION = {
    "in_scope": "SCOPE", "roles": "ROLE", "user_flows": "US",
    "functional_requirements": "FR", "business_rules": "RULE", "nfrs": "NFR",
    "assumptions": "ASM", "open_issues": "ISSUE",
}

BODY_SECTIONS = ("in_scope", "roles", "user_flows", "functional_requirements", "business_rules", "nfrs")

def draft_overview(text: str) -> ProductOverview:
    return ProductOverview(summary=text.strip(), problem="", goals=[])

def section_for(req: Requirement) -> str:
    if req.status == "proposed":
        return "assumptions"
    if req.status == "needs_clarification":
        return "open_issues"
    return SECTION_BY_CATEGORY[req.category]


def build_prd(reviewed: ReviewedRequirements, overview: ProductOverview) -> PRD:
    sections = {name: [] for name in PREFIX_BY_SECTION}
    criteria = []
    # Review에서 Requirement와 연결된 Gap은 그 Requirement의 PRD 항목 출처로 남긴다.
    linked = {}
    for l in reviewed.gap_links:
        linked.setdefault(l.requirement_id, []).append(l.gap)
    for req in reviewed.requirements:
        name = section_for(req)
        number = len(sections[name]) + 1
        gaps = linked.get(req.id, [])
        # 미결정 Requirement에 합쳐진 Gap은 아직 미결정이므로 Gap 설명도 Open Issue 문구에 남긴다.
        description = " / ".join([req.description, *(g.description for g in gaps)]) if name == "open_issues" else req.description
        item = PRDItem(id=f"{PREFIX_BY_SECTION[name]}-{number:03d}", description=description,
                       source_requirement_ids=[req.id], source_gap_ids=[g.id for g in gaps])
        sections[name].append(item)
        if name not in ("assumptions", "open_issues"):
            for text in req.acceptance_criteria:
                criteria.append(AcceptanceCriterion(
                    id=f"AC-{len(criteria) + 1:03d}", description=text,
                    target_id=item.id, source_requirement_ids=[req.id]))
    # 남은 non-blocking Gap은 Requirement로 바꾸지 않고 출처(Gap·관련 Requirement)와 함께 Open Issue로 둔다.
    for gap in reviewed.open_gaps:
        sections["open_issues"].append(PRDItem(
            id=f"ISSUE-{len(sections['open_issues']) + 1:03d}", description=gap.description,
            source_requirement_ids=gap.related_requirement_ids, source_gap_ids=[gap.id]))
    return PRD(project_id=reviewed.project_id, run_id=reviewed.run_id, overview=overview,
               out_of_scope=[], acceptance_criteria=criteria, **sections)


def validate_prd(prd: PRD, reviewed: ReviewedRequirements) -> dict:
    errors, warnings = [], []
    by_id = {r.id: r for r in reviewed.requirements}

    if any(r.blocking and r.status == "needs_clarification" for r in reviewed.requirements):
        errors.append("미해결 Blocking Requirement가 남아 있습니다.")

    for name in BODY_SECTIONS:
        for item in getattr(prd, name):
            if any(i not in by_id or by_id[i].status != "confirmed" for i in item.source_requirement_ids):
                errors.append(f"{item.id}에 확정되지 않은 Requirement가 들어 있습니다.")

    # Gap 출처: 남은 Gap은 Open Issue에, 연결된 Gap은 연결 Requirement의 항목에 있어야 하고 빠져서는 안 된다.
    open_ids = {g.id for g in reviewed.open_gaps}
    link_req = {l.gap.id: l.requirement_id for l in reviewed.gap_links}
    if open_ids & link_req.keys():
        errors.append("같은 Gap이 남은 Gap과 연결된 Gap에 함께 있습니다.")
    for item in prd.open_issues:
        if any(i not in by_id for i in item.source_requirement_ids):
            errors.append(f"{item.id}의 출처 Requirement가 인계물에 없습니다.")
    placed = set()
    for item in [i for name in (*PREFIX_BY_SECTION, "out_of_scope") for i in getattr(prd, name)]:
        for gap_id in item.source_gap_ids:
            if (gap_id in open_ids and item in prd.open_issues) or link_req.get(gap_id) in item.source_requirement_ids:
                placed.add(gap_id)
            else:
                errors.append(f"{item.id}의 출처 Gap {gap_id}가 인계 Gap과 맞지 않습니다.")
    for gap_id in sorted((open_ids | link_req.keys()) - placed):
        errors.append(f"{gap_id}가 PRD 항목 출처에 없습니다.")

    if not prd.overview.summary.strip():
        errors.append("Product Overview가 비어 있습니다.")
    if not prd.roles:
        errors.append("Users / Roles가 비어 있습니다.")
    if not prd.functional_requirements:
        errors.append("Functional Requirements가 비어 있습니다.")

    targets = {ac.target_id for ac in prd.acceptance_criteria}
    for fr in prd.functional_requirements:
        if fr.id not in targets:
            warnings.append(f"{fr.id}에 Acceptance Criteria가 없습니다.")

    return {"passed": not errors, "errors": errors, "warnings": warnings}
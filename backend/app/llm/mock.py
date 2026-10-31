"""Deterministic rules used only by the explicitly selected Mock provider."""
# ponytail: keyword heuristics are demo-only; select SnowChat for semantic analysis.

import re

from app.schemas import ClarificationQuestion, Gap, GapCategory, Requirement, RequirementCategory, UpdatedRequirement
from app.workflow import GAP_TO_REQUIREMENT_CATEGORY, next_requirement_id

# Generic Web SaaS wording. Questions carry keywords analyze_gaps looks for, so an
# answered question resolves its gap on the next analysis.
QUESTION_TEMPLATES: dict[GapCategory, tuple[str, str]] = {
    "user_role": ("서비스를 사용하는 역할과 역할별 목적은 무엇인가요?", "일반 사용자와 관리자 역할을 둔다."),
    "core_flow": ("요청이 접수된 뒤 어떤 순서로 처리되고 완료되나요?", "요청은 접수, 처리, 완료 순서로 진행한다."),
    "permission_approval": ("승인자는 누구이며 승인 권한은 어떤 역할에 있나요?", "관리자가 승인자로서 승인 또는 반려한다."),
    "state_change": ("요청은 어떤 상태(예: 대기, 완료, 반려)를 거치나요?", "요청 상태는 대기에서 완료 또는 반려로 바뀐다."),
    "modify_cancel": ("제출한 요청의 수정·취소는 언제까지, 누가 할 수 있나요?", "처리 전까지 요청자가 수정·취소할 수 있다."),
    "exception_handling": ("중복 요청이나 처리 실패 같은 예외는 어떻게 처리하나요?", "예외가 발생하면 요청을 거부하고 사유를 안내한다."),
    "service_scope": ("이번 서비스 범위에 포함하거나 제외할 기능은 무엇인가요?", "입력에 명시된 기능만 범위에 포함한다."),
    "external_integration": ("반드시 연동해야 하는 외부 시스템이 있나요?", "외부 시스템 연동 없이 제공한다."),
    "business_rule": ("반드시 적용해야 하는 업무 규칙이나 정책은 무엇인가요?", "별도 규칙 없이 일반적인 정책을 따른다."),
    "nfr": ("성능·보안 등 반드시 지켜야 할 조건이 있나요?", "일반적인 웹 서비스 수준의 보안과 성능을 따른다."),
}


def category_for(clause: str) -> RequirementCategory:
    if re.search(r"승인|허가|권한|접근|approve|permission", clause, re.I):
        return "permission"
    if re.search(r"연동|외부 시스템|integration|external API", clause, re.I):
        return "integration"
    if re.search(r"오류|실패|예외|error|failure", clause, re.I):
        return "exception"
    if re.search(r"상태|대기|완료|반려|status", clause, re.I):
        return "state"
    if re.search(r"단계|순서|절차|흐름|workflow", clause, re.I):
        return "flow"
    if re.search(r"보안|성능|응답 시간|암호화|security|performance", clause, re.I):
        return "nfr"
    if re.search(r"규칙|정책|필수|금지|rule|policy", clause, re.I):
        return "business_rule"
    if re.search(r"역할|role", clause, re.I):
        return "role"
    if re.search(r"범위|제외|한정|scope", clause, re.I):
        return "scope"
    return "functional"


def extract_requirements(text: str, project_id: str) -> list[Requirement]:
    clauses = re.split(r"(?<=[.!?。])\s+|(?:하고|그리고|및)\s+", text.strip())
    requirements: list[Requirement] = []
    roles = dict.fromkeys(re.findall(
        r"(?:^|\s)(직원|관리자|사용자|고객|운영자|담당자|회원)(?:들)?(?:이|가|은|는)",
        text,
    ))
    for role in roles:
        requirements.append(Requirement(
            project_id=project_id,
            id=f"REQ-{len(requirements) + 1:03d}",
            category="role",
            description=f"{role} 역할이 존재한다.",
            status="confirmed",
            source="initial_input",
            blocking=False,
        ))
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        category = category_for(clause)
        unresolved = bool(re.search(r"미정|추후 결정|나중에 결정|상황에 따라|적절히|undecided|TBD", clause, re.I))
        requirements.append(Requirement(
            project_id=project_id,
            id=f"REQ-{len(requirements) + 1:03d}",
            category=category,
            description=clause,
            status="needs_clarification" if unresolved else "confirmed",
            source="initial_input",
            blocking=unresolved and category != "nfr",
        ))
    return requirements


def analyze_gaps(requirements: list[Requirement]) -> list[Gap]:
    gaps: list[Gap] = []

    def add(category: GapCategory, description: str, blocking: bool, related: list[str]) -> None:
        gaps.append(Gap(
            id=f"GAP-{len(gaps) + 1:03d}",
            related_requirement_ids=related,
            category=category,
            description=description,
            blocking=blocking,
        ))

    category_to_gap: dict[RequirementCategory, GapCategory] = {
        "role": "user_role", "functional": "core_flow", "flow": "core_flow",
        "permission": "permission_approval", "state": "state_change",
        "business_rule": "business_rule", "exception": "exception_handling",
        "scope": "service_scope", "integration": "external_integration",
        "nfr": "nfr",
    }
    for requirement in requirements:
        if requirement.status == "needs_clarification":
            add(category_to_gap[requirement.category],
                f"명시된 내용의 결정 또는 조건이 불분명합니다: {requirement.description}",
                requirement.blocking, [requirement.id])

    descriptions = " ".join(requirement.description for requirement in requirements)
    request_ids = [r.id for r in requirements if re.search(r"신청|요청|예약|주문|결제|request|book|order|pay", r.description, re.I)]
    if request_ids and not re.search(r"수정|변경|취소|modify|edit|cancel", descriptions, re.I):
        add("modify_cancel", "신청·요청 등의 수정 또는 취소 가능 여부가 명시되지 않았습니다.", True, request_ids)

    approval_ids = [r.id for r in requirements if re.search(r"승인|허가|approve", r.description, re.I)]
    if approval_ids and not re.search(r"관리자|담당자|운영자|승인자|manager|admin|approver", descriptions, re.I):
        add("permission_approval", "승인 권한을 가진 역할이 명시되지 않았습니다.", True, approval_ids)

    if request_ids and not approval_ids and not re.search(r"처리|완료|상태|process|complete|status", descriptions, re.I):
        add("core_flow", "신청·요청 이후의 핵심 처리 흐름이 명시되지 않았습니다.", True, request_ids)

    return gaps


def generate_questions(gaps: list[Gap]) -> list[ClarificationQuestion]:
    return [ClarificationQuestion(gap_id=g.id, question=QUESTION_TEMPLATES[g.category][0],
                                  ai_proposal=QUESTION_TEMPLATES[g.category][1]) for g in gaps if g.blocking]


def update_requirements(payload: dict) -> list[UpdatedRequirement]:
    requirements = [Requirement.model_validate(r) for r in payload["requirements"]]
    updates: list[UpdatedRequirement] = []
    for item in payload["clarifications"]:
        answer = item["answer"].strip()
        if not answer:
            continue
        gap = Gap.model_validate(item["gap"])
        description = f"{answer} (질문: {item['question']})"
        done = {u.id for u in updates}
        linked = [r for r in requirements if r.id in gap.related_requirement_ids
                  and r.status == "needs_clarification" and r.id not in done]
        targets = [(r.id, r.category) for r in linked] or [
            (next_requirement_id([*requirements, *updates]), GAP_TO_REQUIREMENT_CATEGORY[gap.category])]
        updates += [UpdatedRequirement(project_id=payload["project_id"], id=id, category=category,
                                       description=description, status="confirmed", source="clarification_answer",
                                       blocking=False, acceptance_criteria=[]) for id, category in targets]
    return updates


class MockProvider:
    mode = "mock"

    def model_for(self, stage):
        return None

    def complete(self, stage, prompt, payload, output_type, model):
        if stage in ("m2_clarification_plan", "m2_review_completion"):
            from app.m2.requirements_mock import plan, complete_review
            result = plan(payload) if stage == "m2_clarification_plan" else complete_review(payload)
            return output_type.model_validate(result).model_dump_json(), None
        handlers = {
            "requirement_extraction": lambda: {"requirements": extract_requirements(payload["text"], payload["project_id"])},
            "gap_analysis": lambda: {"gaps": analyze_gaps([Requirement.model_validate(r) for r in payload["requirements"]])},
            "question_generation": lambda: {"questions": generate_questions([Gap.model_validate(g) for g in payload["gaps"]])},
            "requirement_update": lambda: {"requirements": update_requirements(payload)},
        }
        result = {key: [item.model_dump() for item in items] for key, items in handlers[stage]().items()}
        return output_type.model_validate(result).model_dump_json(), None

"""Clarification·Review 상태 전이 규칙. LLM을 호출하지 않는 순수 함수만 둔다."""
from app.schemas import (
    AddAction, Answer, ClarificationQuestion, EditAction, Gap, Requirement, RequirementCategory,
    ResolveGapAction, ReviewedRequirements, UpdatedRequirement, WorkflowState,
)

MAX_ROUNDS = 3

GAP_TO_REQUIREMENT_CATEGORY: dict[str, RequirementCategory] = {
    "user_role": "role", "core_flow": "flow", "permission_approval": "permission",
    "state_change": "state", "modify_cancel": "business_rule", "exception_handling": "exception",
    "service_scope": "scope", "external_integration": "integration",
    "business_rule": "business_rule", "nfr": "nfr",
}


def require_phase(state: WorkflowState, phase: str) -> None:
    if state.phase != phase:
        raise ValueError(f"현재 단계({state.phase})에서는 허용되지 않는 작업입니다.")


def next_step(gaps: list[Gap], round: int) -> str:
    if not any(g.blocking for g in gaps):
        return "review"
    return "question" if round < MAX_ROUNDS else "proposal"


def next_requirement_id(reqs: list[Requirement]) -> str:
    numbers = [int(r.id[4:]) for r in reqs if r.id.startswith("REQ-") and r.id[4:].isdigit()]
    return f"REQ-{max(numbers, default=0) + 1:03d}"


def apply_update(reqs: list[Requirement], updates: list[UpdatedRequirement], project_id: str) -> list[Requirement]:
    by_id = {r.id: r for r in reqs}
    if len({u.id for u in updates}) != len(updates):
        raise ValueError("갱신 결과에 중복 id가 있습니다.")
    for u in updates:
        if u.project_id != project_id:
            raise ValueError("갱신 결과의 project_id가 일치하지 않습니다.")
        # 최초 입력·Review 입력·AI Proposal의 출처를 지키기 위해 미결정 항목과 이전 답변 항목만 교체한다.
        old = by_id.get(u.id)
        if old and not (old.status == "needs_clarification"
                        or (old.status == "confirmed" and old.source == "clarification_answer")):
            raise ValueError("답변 반영은 미결정 항목이나 이전 답변 항목만 교체할 수 있습니다.")
    for u in updates:
        by_id[u.id] = Requirement(**u.model_dump())
    return list(by_id.values())


def to_proposals(reqs: list[Requirement], gaps: list[Gap],
                 questions: list[ClarificationQuestion], project_id: str) -> list[Requirement]:
    proposals = {q.gap_id: q.ai_proposal for q in questions}
    result = list(reqs)
    for g in (g for g in gaps if g.blocking):
        if g.id not in proposals:
            raise ValueError(f"{g.id}에 대한 AI Proposal이 없습니다.")
        changes = {"status": "proposed", "source": "ai_proposal", "blocking": False,
                   "description": proposals[g.id]}
        linked = [i for i, r in enumerate(result)
                  if r.id in g.related_requirement_ids and r.status == "needs_clarification"]
        if linked:  # 경우 A: 미결정 항목을 id 그대로 Proposal로 전환
            first, *rest = linked
            result[first] = result[first].model_copy(update=changes)
            for i in rest:
                result[i] = result[i].model_copy(update={"blocking": False})
        else:  # 경우 B: 새 Proposal 추가
            result.append(Requirement(project_id=project_id, id=next_requirement_id(result),
                                      category=GAP_TO_REQUIREMENT_CATEGORY[g.category], **changes))
    return result


def accept(reqs: list[Requirement], ids: list[str]) -> list[Requirement]:
    by_id = {r.id: r for r in reqs}
    if not ids or any(by_id.get(i) is None or by_id[i].status != "proposed" for i in ids):
        raise ValueError("수락할 수 있는 AI Proposal이 아닙니다.")
    return [r.model_copy(update={"status": "confirmed", "blocking": False}) if r.id in ids else r for r in reqs]


def edit(state: WorkflowState, action: EditAction) -> WorkflowState:
    if action.id not in {r.id for r in state.requirements}:
        raise ValueError(f"{action.id} Requirement가 없습니다.")
    changes = {k: v for k in ("description", "category", "acceptance_criteria")
               if (v := getattr(action, k)) is not None}
    gaps = state.gaps
    if action.confirm:
        changes |= {"status": "confirmed", "blocking": False}
        # 미결정 항목을 사용자가 확정하면 그 항목에 연결된 Gap도 해소된 것으로 본다.
        if next(r for r in state.requirements if r.id == action.id).status == "needs_clarification":
            gaps = [g for g in gaps if action.id not in g.related_requirement_ids]
    reqs = [r.model_copy(update=changes) if r.id == action.id else r for r in state.requirements]
    return state.model_copy(update={"requirements": reqs, "gaps": gaps, "edit_count": state.edit_count + 1})


def add(state: WorkflowState, action: AddAction) -> WorkflowState:
    new = Requirement(project_id=state.project_id, id=next_requirement_id(state.requirements),
                      category=action.category, description=action.description, status="confirmed",
                      source="review_input", blocking=False, acceptance_criteria=action.acceptance_criteria)
    return state.model_copy(update={"requirements": [*state.requirements, new], "edit_count": state.edit_count + 1})


def resolve_gap(state: WorkflowState, action: ResolveGapAction) -> WorkflowState:
    """사용자가 입력한 결정을 Requirement로 기록하고 Gap을 인계 대상에서 뺀다. 결정 없이 Gap만 지우는 경로는 없다."""
    gap = next((g for g in open_gaps(state) if g.id == action.gap_id), None)
    if gap is None:
        raise ValueError(f"{action.gap_id}는 해결할 수 있는 남은 Gap이 아닙니다.")
    if not action.description.strip():
        raise ValueError("결정 내용을 입력해야 합니다.")
    added = add(state, AddAction(type="add", category=action.category or GAP_TO_REQUIREMENT_CATEGORY[gap.category],
                                 description=action.description, acceptance_criteria=action.acceptance_criteria))
    return added.model_copy(update={"gaps": [g for g in state.gaps if g.id != gap.id]})


def open_gaps(state: WorkflowState) -> list[Gap]:
    """PRD Open Issues로 넘길 Gap. blocking Gap은 질문·Proposal로 처리됐고,
    needs_clarification에 연결된 Gap은 그 Requirement가 Open Issue로 전달되므로 제외한다."""
    unresolved = {r.id for r in state.requirements if r.status == "needs_clarification"}
    return [g for g in state.gaps if not g.blocking and not unresolved & set(g.related_requirement_ids)]


def approve(state: WorkflowState) -> tuple[WorkflowState, ReviewedRequirements]:
    if any(r.blocking and r.status == "needs_clarification" for r in state.requirements):
        raise ValueError("해소되지 않은 Blocking Requirement가 있습니다.")
    reviewed = ReviewedRequirements(
        project_id=state.project_id, run_id=state.run_id, requirements=state.requirements,
        open_gaps=open_gaps(state), metrics={
            "question_count": sum(len(r.questions) for r in state.history),
            "turn_count": len(state.history),
            "edit_count": state.edit_count,
            "unaccepted_proposal_count": sum(r.status == "proposed" for r in state.requirements),
        })
    return state.model_copy(update={"phase": "approved"}), reviewed


def validate_state(state: WorkflowState) -> None:
    """클라이언트가 보낸 상태의 불변식. stateless 왕복이라 요청마다 다시 확인한다."""
    ids = [r.id for r in state.requirements]
    if len(ids) != len(set(ids)) or any(r.project_id != state.project_id for r in state.requirements):
        raise ValueError("Requirement의 id가 중복되거나 project_id가 일치하지 않습니다.")
    if not {q.gap_id for q in state.questions} <= {g.id for g in state.gaps}:
        raise ValueError("질문이 현재 Gap 목록에 없는 Gap을 가리킵니다.")


def fill_answers(questions: list[ClarificationQuestion], answers: list[Answer] | None) -> list[Answer]:
    """답변을 질문 순서로 정렬하고, 빠진 질문은 빈 답변(건너뜀)으로 채운다."""
    if answers is None:
        raise ValueError("현재 질문에 대한 답변이 필요합니다.")
    ids = [a.gap_id for a in answers]
    if len(ids) != len(set(ids)) or not set(ids) <= {q.gap_id for q in questions}:
        raise ValueError("답변의 gap_id가 중복되거나 현재 질문 목록에 없습니다.")
    given = {a.gap_id: a.answer for a in answers}
    return [Answer(gap_id=q.gap_id, answer=given.get(q.gap_id, "")) for q in questions]

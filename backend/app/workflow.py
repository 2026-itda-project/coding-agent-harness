"""Clarification·Review 상태 전이 규칙. LLM을 호출하지 않는 순수 함수만 둔다."""
from app.schemas import (
    AddAction, Answer, ClarificationQuestion, EditAction, Gap, Requirement, RequirementCategory,
    GapLink, LinkGapAction, ResolveGapAction, ReviewedRequirements, UnlinkGapAction, UpdatedRequirement, WorkflowState,
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
    if action.confirm:
        changes |= {"status": "confirmed", "blocking": False}
    reqs = [r.model_copy(update=changes) if r.id == action.id else r for r in state.requirements]
    return state.model_copy(update={"requirements": reqs, "edit_count": state.edit_count + 1})


def add(state: WorkflowState, action: AddAction) -> WorkflowState:
    new = Requirement(project_id=state.project_id, id=next_requirement_id(state.requirements),
                      category=action.category, description=action.description, status="confirmed",
                      source="review_input", blocking=False, acceptance_criteria=action.acceptance_criteria)
    return state.model_copy(update={"requirements": [*state.requirements, new], "edit_count": state.edit_count + 1})


def open_gaps(state: WorkflowState) -> list[Gap]:
    """PRD Open Issues로 넘길 Gap. blocking Gap은 질문·Proposal로 처리됐다. 관련 Requirement가 있다는 것만으로는
    같은 내용이라고 보지 않으므로, 사용자가 연결하지 않은 non-blocking Gap은 모두 넘긴다."""
    return [g for g in state.gaps if not g.blocking]


def suggested_requirement_id(gap: Gap) -> str | None:
    """추천 연결 대상. 관련 Requirement가 하나뿐일 때만 추천하고, 반영은 사용자가 확인해야 한다."""
    return gap.related_requirement_ids[0] if len(gap.related_requirement_ids) == 1 else None


def attach_gaps(state: WorkflowState, gap_ids: set[str], target: dict[str, str]) -> WorkflowState:
    links = [GapLink(gap=g, requirement_id=target[g.id]) for g in state.gaps if g.id in gap_ids]
    return state.model_copy(update={"gaps": [g for g in state.gaps if g.id not in gap_ids],
                                    "gap_links": [*state.gap_links, *links]})


def resolve_gap(state: WorkflowState, action: ResolveGapAction) -> WorkflowState:
    """사용자가 입력한 결정을 Requirement로 기록하고 Gap을 그 Requirement에 연결한다. 결정 없이 Gap만 지우는 경로는 없다."""
    gap = next((g for g in open_gaps(state) if g.id == action.gap_id), None)
    if gap is None:
        raise ValueError(f"{action.gap_id}는 해결할 수 있는 남은 Gap이 아닙니다.")
    if not action.description.strip():
        raise ValueError("결정 내용을 입력해야 합니다.")
    added = add(state, AddAction(type="add", category=action.category or GAP_TO_REQUIREMENT_CATEGORY[gap.category],
                                 description=action.description, acceptance_criteria=action.acceptance_criteria))
    return attach_gaps(added, {gap.id}, {gap.id: added.requirements[-1].id})


def link_gap(state: WorkflowState, action: LinkGapAction) -> WorkflowState:
    """사용자가 확인한 추천 연결만 반영한다. 연결된 Gap은 해당 Requirement의 PRD 항목 출처로 이어진다."""
    gaps = {g.id: g for g in open_gaps(state)}
    req_ids = {r.id for r in state.requirements}
    target = {i: suggested_requirement_id(gaps[i]) for i in action.gap_ids if i in gaps}
    if (not action.gap_ids or len(set(action.gap_ids)) != len(action.gap_ids) or len(target) != len(action.gap_ids)
            or any(t not in req_ids for t in target.values())):
        raise ValueError("추천 연결 대상이 있는 남은 Gap이 아닙니다.")
    return attach_gaps(state, set(action.gap_ids), target)


def unlink_gap(state: WorkflowState, action: UnlinkGapAction) -> WorkflowState:
    """잘못 연결한 Gap을 되돌린다. 결정 입력으로 만든 Requirement는 사용자 입력이므로 그대로 둔다."""
    found = next((l for l in state.gap_links if l.gap.id == action.gap_id), None)
    if found is None:
        raise ValueError(f"{action.gap_id}는 연결된 Gap이 아닙니다.")
    return state.model_copy(update={"gaps": [*state.gaps, found.gap],
                                    "gap_links": [l for l in state.gap_links if l is not found]})


def approve(state: WorkflowState) -> tuple[WorkflowState, ReviewedRequirements]:
    if any(r.blocking and r.status == "needs_clarification" for r in state.requirements):
        raise ValueError("해소되지 않은 Blocking Requirement가 있습니다.")
    reviewed = ReviewedRequirements(
        project_id=state.project_id, run_id=state.run_id, requirements=state.requirements,
        open_gaps=open_gaps(state), gap_links=state.gap_links, metrics={
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
    linked = [l.gap.id for l in state.gap_links]
    if (len(linked) != len(set(linked)) or set(linked) & {g.id for g in state.gaps}
            or any(l.requirement_id not in ids for l in state.gap_links)):
        raise ValueError("Gap 연결 기록이 현재 Gap·Requirement와 맞지 않습니다.")


def fill_answers(questions: list[ClarificationQuestion], answers: list[Answer] | None) -> list[Answer]:
    """답변을 질문 순서로 정렬하고, 빠진 질문은 빈 답변(건너뜀)으로 채운다."""
    if answers is None:
        raise ValueError("현재 질문에 대한 답변이 필요합니다.")
    ids = [a.gap_id for a in answers]
    if len(ids) != len(set(ids)) or not set(ids) <= {q.gap_id for q in questions}:
        raise ValueError("답변의 gap_id가 중복되거나 현재 질문 목록에 없습니다.")
    given = {a.gap_id: a.answer for a in answers}
    return [Answer(gap_id=q.gap_id, answer=given.get(q.gap_id, "")) for q in questions]

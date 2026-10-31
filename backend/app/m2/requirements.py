"""M2 single-round clarification. Models choose content; code checks provenance and coverage.
Legacy workflows keep their old policy and saved-run identity.
"""
from pydantic import BaseModel, ConfigDict, Field

from app.schemas import (Answer, ClarificationQuestion, ClarificationRound, Gap, GapLink,
                         ProposalConversion, Requirement)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class QuestionPlan(Strict):
    # A service-wide role choice can be absent from the original gap analysis.
    role_gap: Gap | None
    questions: list[ClarificationQuestion] = Field(max_length=5)


class Resolution(Strict):
    gap_id: str
    requirement_id: str


class ReviewDraft(Strict):
    requirements: list[Requirement]
    resolutions: list[Resolution]
    selected_role_id: str
    out_of_scope_ids: list[str]


def validate_review(state):
    if state.clarification_round not in (0, 1) or len(state.history) > 1 or len(state.questions) > 5:
        raise ValueError("질문은 최대 1라운드, 5개입니다.")
    gap_ids = [g.id for g in state.gaps]
    ids = {r.id for r in state.requirements}
    if len(gap_ids) != len(set(gap_ids)) or any(not set(g.related_requirement_ids) <= ids for g in state.gaps):
        raise ValueError("Gap 식별자 또는 요구사항 참조가 올바르지 않습니다.")
    if state.questions and (state.clarification_round != 1 or state.history):
        raise ValueError("질문 라운드가 상태와 맞지 않습니다.")
    if len({q.gap_id for q in state.questions}) != len(state.questions):
        raise ValueError("같은 Gap을 중복 질문할 수 없습니다.")
    if any(h.round != 1 or len(h.questions) > 5 for h in state.history):
        raise ValueError("질문 이력이 1라운드 정책과 맞지 않습니다.")
    if any(c.round not in (0, 1) for c in state.proposal_conversions):
        raise ValueError("전환 라운드가 정책과 맞지 않습니다.")
    if state.phase not in ("review", "approved"):
        return
    by_id = {r.id: r for r in state.requirements}
    role = by_id.get(state.selected_role_id)
    if not role or role.category != "role" or role.id in state.out_of_scope_ids:
        raise ValueError("데모에서 사용할 역할 하나를 선택해야 합니다.")
    if len(set(state.out_of_scope_ids)) != len(state.out_of_scope_ids) or not set(state.out_of_scope_ids) <= by_id.keys():
        raise ValueError("범위 제외 항목이 요구사항과 맞지 않습니다.")
    if state.gaps or any(r.status == "needs_clarification" or r.blocking for r in state.requirements):
        raise ValueError("검토 전에 미정 사항을 보완해야 합니다.")
    if any(r.status == "proposed" and r.source != "ai_proposal" for r in state.requirements):
        raise ValueError("미확정 기본값은 AI 출처를 유지해야 합니다.")
    if any(r.category == "role" and r.id != role.id and r.id not in state.out_of_scope_ids for r in state.requirements):
        raise ValueError("선택하지 않은 역할은 구현 범위에서 제외해야 합니다.")


def clarify(state, answers, provider):
    from app.pipeline import run_stage
    if state.questions:
        given = {a.gap_id: a for a in answers or []}
        if answers is None or len(given) != len(answers) or not given.keys() <= {q.gap_id for q in state.questions}:
            raise ValueError("답변 식별자가 현재 질문과 맞지 않습니다.")
        normalized = []
        for q in state.questions:
            a = given.get(q.gap_id, Answer(gap_id=q.gap_id, answer="", decision="skip"))
            if a.decision == "reject" and not a.answer.strip():
                raise ValueError("거절한 추천안 대신 원하는 내용을 적어 주세요.")
            if a.decision == "accept":
                a = a.model_copy(update={"answer": q.ai_proposal})
            elif a.decision == "skip":
                a = a.model_copy(update={"answer": ""})
            elif not a.answer.strip():
                a = a.model_copy(update={"decision": "skip"})
            normalized.append(a)
        history = [ClarificationRound(round=1, gaps=state.gaps, questions=state.questions, answers=normalized)]
        return finish_review(state.model_copy(update={"history": history, "questions": []}), provider)
    if answers is not None or state.history or state.clarification_round:
        raise ValueError("추가 질문 라운드는 만들지 않습니다.")
    log = []
    def check(plan):
        gaps = {g.id: g for g in state.gaps}
        if plan.role_gap:
            g = plan.role_gap
            if g.id in gaps or g.category != "user_role" or not g.blocking or not g.description.strip():
                raise ValueError("역할 선택 Gap이 올바르지 않습니다.")
            if not set(g.related_requirement_ids) <= {r.id for r in state.requirements}:
                raise ValueError("역할 Gap 참조가 올바르지 않습니다.")
            gaps[g.id] = g
        ids = [q.gap_id for q in plan.questions]
        if len(ids) != len(set(ids)) or any(i not in gaps or not gaps[i].blocking for i in ids):
            raise ValueError("질문은 현재 핵심 Gap을 참조해야 합니다.")
        if any(not q.question.strip() or not q.ai_proposal.strip() for q in plan.questions):
            raise ValueError("질문과 추천 답변이 필요합니다.")
        if len([r for r in state.requirements if r.category == "role"]) > 1:
            if not any(gaps[i].category == "user_role" for i in ids):
                raise ValueError("여러 역할 중 사용할 역할을 같은 라운드에서 확인해야 합니다.")
    plan = run_stage(provider, "m2_clarification_plan", state.model_dump(), QuestionPlan, state.run_id, log, check)
    state = state.model_copy(update={"gaps": [*state.gaps, *([plan.role_gap] if plan.role_gap else [])],
                                    "usage": [*state.usage, *log]})
    if plan.questions:
        return state.model_copy(update={"questions": plan.questions, "clarification_round": 1,
                                        "clarification_needed": True})
    return finish_review(state, provider)


def finish_review(state, provider, role_override=None):
    from app.pipeline import run_stage
    log = []
    original = {r.id: r for r in state.requirements}
    gaps = {g.id: g for g in state.gaps}
    answers = {a.gap_id: a for h in state.history for a in h.answers}
    questions = {q.gap_id: q for h in state.history for q in h.questions}

    def check(draft):
        reqs = {r.id: r for r in draft.requirements}
        if len(reqs) != len(draft.requirements) or not original.keys() <= reqs.keys():
            raise ValueError("요구사항 삭제 또는 중복 ID")
        resolutions = {r.gap_id: r.requirement_id for r in draft.resolutions}
        if len(resolutions) != len(draft.resolutions) or resolutions.keys() != gaps.keys():
            raise ValueError("서비스 전반의 Gap을 포함해 모든 Gap을 처리해야 합니다.")
        if any(rid not in reqs for rid in resolutions.values()):
            raise ValueError("Gap 결정 참조 누락")
        for r in draft.requirements:
            if r.project_id != state.project_id or not r.id.strip() or not r.description.strip():
                raise ValueError("요구사항 식별자/내용 오류")
            old = original.get(r.id)
            if old and old.status != "needs_clarification" and old != r:
                raise ValueError("기존 결정과 출처를 임의 변경할 수 없습니다.")
            if old == r:
                continue
            related = [gid for gid, rid in resolutions.items() if rid == r.id or r.id in gaps[gid].related_requirement_ids]
            accepted = [gid for gid in related if answers.get(gid) and answers[gid].decision == "accept"]
            written = [gid for gid in related if answers.get(gid) and answers[gid].decision in ("answer", "reject")]
            if r.source == "ai_proposal":
                if r.status == "confirmed" and not any(r.description == questions[gid].ai_proposal for gid in accepted):
                    raise ValueError("수락하지 않은 AI 제안을 확정할 수 없습니다.")
                if r.status not in ("proposed", "confirmed"):
                    raise ValueError("AI 보완 상태 오류")
            elif r.source != "clarification_answer" or r.status != "confirmed" or not written:
                raise ValueError("사용자 답변 없이 사용자 출처를 만들 수 없습니다.")
        for gid, rid in resolutions.items():
            r = reqs[rid]; a = answers.get(gid)
            if a and a.decision == "accept":
                if (r.description, r.status, r.source) != (questions[gid].ai_proposal, "confirmed", "ai_proposal"):
                    raise ValueError("수락한 추천안을 바꾸면 안 됩니다.")
            elif a and a.decision in ("answer", "reject"):
                if r.status != "confirmed" or r.source != "clarification_answer":
                    raise ValueError("직접 작성한 답변의 출처를 보존해야 합니다.")
            elif r.status != "proposed" or r.source != "ai_proposal":
                raise ValueError("미응답/미질문 Gap은 미확정 AI 기본값이어야 합니다.")
        for gid, rid in resolutions.items():
            if answers.get(gid) and answers[gid].decision == "reject":
                targets = {rid, *gaps[gid].related_requirement_ids}
                if any(reqs[i].description == questions[gid].ai_proposal for i in targets):
                    raise ValueError("거절한 추천안을 다시 적용할 수 없습니다.")
        role_gaps = [g for g in gaps.values() if g.category == "user_role" and g.id in questions]
        # A role-choice decision may describe scope while selecting an existing role from the same gap.
        role_choices = {resolutions[g.id] for g in role_gaps}
        role_choices.update(rid for g in role_gaps for rid in g.related_requirement_ids
                            if original[rid].category == "role")
        if role_gaps and not role_override and draft.selected_role_id not in role_choices:
            raise ValueError("질문에서 정한 역할을 선택해야 합니다.")
        if role_override and draft.selected_role_id != role_override:
            raise ValueError("검토에서 수정한 역할을 변경할 수 없습니다.")
        validate_review(state.model_copy(update={"phase": "review", "requirements": draft.requirements,
            "gaps": [], "questions": [], "selected_role_id": draft.selected_role_id,
            "out_of_scope_ids": draft.out_of_scope_ids}))

    payload = {**state.model_dump(), "role_override": role_override}
    draft = run_stage(provider, "m2_review_completion", payload, ReviewDraft, state.run_id, log, check)
    conversions = list(state.proposal_conversions)
    links = list(state.gap_links)
    for resolution in draft.resolutions:
        g = gaps[resolution.gap_id]; a = answers.get(g.id)
        if a and a.decision in ("answer", "reject", "accept"):
            links.append(GapLink(gap=g, requirement_id=resolution.requirement_id))
        else:
            reason = "round_limit" if g.id in questions else "question_limit" if g.blocking else "ai_default"
            conversions.append(ProposalConversion(gap=g, requirement_id=resolution.requirement_id,
                                                  reason=reason, round=state.clarification_round))
    return state.model_copy(update={"requirements": draft.requirements, "gaps": [], "questions": [],
        "phase": "review", "clarification_needed": False, "selected_role_id": draft.selected_role_id,
        "out_of_scope_ids": draft.out_of_scope_ids, "proposal_conversions": conversions,
        "gap_links": links, "usage": [*state.usage, *log]})

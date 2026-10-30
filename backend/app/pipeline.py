import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError

from app.llm.claude import ClaudeProvider
from app.llm.errors import AnalysisError
from app.llm.mock import MockProvider
from app.llm.snowchat import SnowChatProvider
from app.schemas import (
    AnalyzeResponse, ClarificationRound, ExtractionOutput, GapOutput, QuestionOutput, RequirementUpdateOutput,
)
from app.workflow import (
    accept, add, apply_update, approve, edit, fill_answers, link_gap, next_step, require_phase, resolve_gap,
    to_proposals, unlink_gap, validate_state,
)

logger = logging.getLogger("uvicorn.error")
PROMPTS = Path(__file__).parent / "prompts"


def get_provider():
    mode = os.getenv("LLM_PROVIDER", "mock")
    if mode == "mock":
        return MockProvider()
    if mode == "snowchat":
        return SnowChatProvider()
    if mode == "claude":
        return ClaudeProvider()
    raise AnalysisError("invalid_config", "LLM_PROVIDER는 mock, snowchat, claude 중 하나여야 합니다.", 503)


def record_usage(record, usage):
    if not isinstance(usage, dict):
        return
    record["usage"] = usage
    # SnowChat reports prompt/completion tokens; Claude reports input/output tokens and a computed cost.
    for source, target in [("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"),
                           ("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")]:
        if isinstance(usage.get(source), int):
            record[target] = usage[source]
    if usage.get("cost_usd") is not None:
        record["cost"] = usage["cost_usd"]


def run_stage(provider, stage, payload, output_type, run_id, records, check=None):
    started = perf_counter()
    project_id = payload.get("project_id") or next((r["project_id"] for r in payload.get("requirements", [])), None)
    record = {"project_id": project_id, "run_id": run_id, "stage": stage, "model": None, "success": False,
              "cost": None, "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        record["model"] = provider.model_for(stage)
        content, usage = provider.complete(stage, (PROMPTS / f"{stage}.md").read_text(), payload, output_type, record["model"])
        record_usage(record, usage)
        output = output_type.model_validate_json(content)
        if stage == "requirement_extraction":
            ids = [r.id for r in output.requirements]
            if len(ids) != len(set(ids)) or any(r.project_id != payload["project_id"] or not r.id.strip() or not r.description.strip() for r in output.requirements):
                raise AnalysisError("structured_output_invalid", "요구사항 식별자 또는 내용 검증에 실패했습니다.")
        elif stage == "gap_analysis":
            ids = [g.id for g in output.gaps]
            known = {r["id"] for r in payload["requirements"]}
            if len(ids) != len(set(ids)) or any(not g.id.strip() or not g.description.strip() or not set(g.related_requirement_ids) <= known for g in output.gaps):
                raise AnalysisError("structured_output_invalid", "Gap 식별자 또는 요구사항 연결 검증에 실패했습니다.")
            covered = {rid for gap in output.gaps if gap.blocking for rid in gap.related_requirement_ids}
            if any(r["blocking"] and r["id"] not in covered for r in payload["requirements"]):
                raise AnalysisError("structured_output_invalid", "미결정 핵심 요구사항에 연결된 Blocking Gap이 누락됐습니다.")
        if check is not None:
            try:
                check(output)
            except ValueError:  # also Pydantic errors, whose text may hold user input: keep the message fixed
                raise AnalysisError("structured_output_invalid", "단계 출력이 검증 규칙을 충족하지 않습니다.") from None
        record["success"] = True
        return output
    except ValidationError as exc:
        record["error"] = "structured_output_invalid"
        # Log locations/types only: Pydantic input/context can contain user text or secrets.
        record["validation_errors"] = [{"loc": e["loc"], "type": e["type"]} for e in exc.errors()]
        raise AnalysisError("structured_output_invalid", "LLM 결과가 출력 Schema를 충족하지 않습니다.") from None
    except AnalysisError as exc:
        record["error"] = exc.code
        record_usage(record, getattr(exc, "usage", None))
        raise
    finally:
        record["latency"] = round(perf_counter() - started, 3)
        record["ended_at"] = datetime.now(timezone.utc).isoformat()
        records.append(record)
        logger.info("analysis_stage %s", json.dumps(record, ensure_ascii=False))


def extract_stage(provider, text, project_id, run_id, records):
    return run_stage(provider, "requirement_extraction", {"text": text, "project_id": project_id}, ExtractionOutput, run_id, records).requirements


def gap_stage(provider, text, requirements, run_id, records):
    return run_stage(provider, "gap_analysis", {"text": text, "requirements": [r.model_dump() for r in requirements]}, GapOutput, run_id, records).gaps


def question_stage(provider, text, requirements, gaps, project_id, run_id, records):
    blocking = [g for g in gaps if g.blocking]

    def check(output):
        ids = [q.gap_id for q in output.questions]
        if len(ids) != len(set(ids)) or set(ids) != {g.id for g in blocking} or any(
                not q.question.strip() or not q.ai_proposal.strip() for q in output.questions):
            raise ValueError("질문이 Blocking Gap과 일대일로 대응하지 않습니다.")

    payload = {"text": text, "project_id": project_id, "requirements": [r.model_dump() for r in requirements],
               "gaps": [g.model_dump() for g in blocking]}
    return run_stage(provider, "question_generation", payload, QuestionOutput, run_id, records, check).questions


def update_stage(provider, text, requirements, gaps, questions, answers, project_id, run_id, records):
    def check(output):
        if any(not r.id.strip() or not r.description.strip() for r in output.requirements):
            raise ValueError("갱신된 요구사항의 식별자 또는 내용이 비어 있습니다.")
        # AC는 사용자가 Review에서 입력한다. LLM이 만든 AC가 사용자 확인 없이 confirmed에 들어가지 않게 한다.
        if any(r.acceptance_criteria for r in output.requirements):
            raise ValueError("답변 반영 결과에 Acceptance Criteria가 있습니다.")
        apply_update(requirements, output.requirements, project_id)

    gaps_by_id = {g.id: g for g in gaps}
    answer_by_id = {a.gap_id: a.answer for a in answers}
    clarifications = [{"gap": gaps_by_id[q.gap_id].model_dump(), "question": q.question,
                       "answer": answer_by_id.get(q.gap_id, "")} for q in questions]
    payload = {"text": text, "project_id": project_id, "requirements": [r.model_dump() for r in requirements],
               "clarifications": clarifications}
    return run_stage(provider, "requirement_update", payload, RequirementUpdateOutput, run_id, records, check).requirements


def analyze(text, provider=None, policy="legacy"):
    provider = provider if provider is not None else get_provider()
    project_id, run_id = str(uuid4()), str(uuid4())
    records = []
    requirements = extract_stage(provider, text, project_id, run_id, records)
    gaps = gap_stage(provider, text, requirements, run_id, records)
    return AnalyzeResponse(policy=policy, project_id=project_id, run_id=run_id, requirements=requirements,
                           gaps=gaps, clarification_needed=any(g.blocking for g in gaps),
                           analysis_mode=provider.mode, usage=records)


def clarify_step(state, answers=None, provider=None):
    """답변 반영 → Gap 재분석 → 다음 질문 / Proposal 전환 / Review 이동. 규칙 위반은 ValueError."""
    require_phase(state, "clarifying")
    validate_state(state)
    if state.policy == "single_round_v1":
        from app.m2.requirements import clarify
        return clarify(state, answers, provider if provider is not None else get_provider())
    reqs, gaps, history, round = state.requirements, state.gaps, state.history, state.clarification_round
    if state.questions:
        answers = fill_answers(state.questions, answers)
        history = [*history, ClarificationRound(round=round, gaps=gaps, questions=state.questions, answers=answers)]
    elif answers is not None:
        raise ValueError("답변할 질문이 없습니다.")
    provider = provider if provider is not None else get_provider()
    records = []
    args = (state.project_id, state.run_id, records)
    # 모두 빈 답변이면 Requirement가 바뀌지 않으므로 update·gap 재분석을 건너뛰고 기존 Gap을 쓴다.
    if state.questions and any(a.answer.strip() for a in answers):
        reqs = apply_update(reqs, update_stage(provider, state.text, reqs, gaps, state.questions, answers, *args),
                            state.project_id)
        gaps = gap_stage(provider, state.text, reqs, state.run_id, records)
    changes = {"requirements": reqs, "gaps": gaps, "history": history, "questions": [],
               "phase": "review", "clarification_needed": False}
    match next_step(gaps, round):
        case "question":
            changes |= {"questions": question_stage(provider, state.text, reqs, gaps, *args),
                        "clarification_round": round + 1, "phase": "clarifying", "clarification_needed": True}
        case "proposal":
            questions = question_stage(provider, state.text, reqs, gaps, *args)  # ai_proposal만 쓴다
            changes["requirements"], conversions = to_proposals(reqs, gaps, questions, state.project_id)
            changes["proposal_conversions"] = [*state.proposal_conversions, *conversions]
    changes["usage"] = [*state.usage, *records]
    return state.model_copy(update=changes)


def review_step(state, action):
    """Review 작업. 새 정책의 역할 수정만 LLM으로 범위를 재정리한다. approve는 인계물도 반환한다."""
    require_phase(state, "review")
    validate_state(state)
    if action.type == "accept":
        return state.model_copy(update={"requirements": accept(state.requirements, action.ids)}), None
    if action.type == "edit":
        updated = edit(state, action)
        validate_state(updated)
        if state.policy == "single_round_v1" and action.id == state.selected_role_id:
            from app.m2.requirements import finish_review
            updated = finish_review(updated, get_provider(), role_override=action.id)
        validate_state(updated)
        return updated, None
    if action.type == "add":
        return add(state, action), None
    if action.type == "resolve_gap":
        return resolve_gap(state, action), None
    if action.type == "link_gap":
        return link_gap(state, action), None
    if action.type == "unlink_gap":
        return unlink_gap(state, action), None
    return approve(state)

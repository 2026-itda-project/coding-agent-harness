import json
import unittest
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from app.schemas import (
    AddAction, ClarificationQuestion, ClarificationRound, EditAction, Gap, Requirement, ResolveGapAction,
    ReviewAction, ReviewedRequirements, UpdatedRequirement, WorkflowState,
)
from app.workflow import (
    MAX_ROUNDS, accept, add, apply_update, approve, edit, next_step, require_phase, resolve_gap, to_proposals,
)

HANDOFF = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/examples/snowchat-handoff.json").read_text(encoding="utf-8"))
PID = HANDOFF["project_id"]


def handoff_state(**changes):
    return WorkflowState(**{**HANDOFF, "text": "직원들이 휴가를 신청하고 관리자가 승인할 수 있는 사내 휴가관리 서비스를 만들어줘.",
                            **changes})


def req(id, status="confirmed", blocking=False, source="initial_input", category="functional", project_id=PID):
    return Requirement(project_id=project_id, id=id, category=category, description=f"{id} 내용",
                       status=status, source=source, blocking=blocking)


def updated(id, status="confirmed", project_id=PID):
    return UpdatedRequirement(project_id=project_id, id=id, category="business_rule", description=f"{id} 답변",
                              status=status, source="clarification_answer", blocking=False, acceptance_criteria=[])


def gap(id, related, blocking=True, category="modify_cancel"):
    return Gap(id=id, related_requirement_ids=related, category=category, description="미정", blocking=blocking)


def question(gap_id):
    return ClarificationQuestion(gap_id=gap_id, question="어떻게 하나요?", ai_proposal=f"{gap_id} 기본값을 적용한다.")


def proposal_state():
    state = handoff_state(phase="review", clarification_round=MAX_ROUNDS)
    blocking = [g for g in state.gaps if g.blocking]
    reqs = to_proposals(state.requirements, blocking, [question(g.id) for g in blocking], PID)
    return state.model_copy(update={"requirements": reqs})


class NextStepTest(unittest.TestCase):
    def test_transitions(self):
        blocking = [gap("GAP-001", ["REQ-001"])]
        self.assertEqual(next_step([], 0), "review")
        for round in range(MAX_ROUNDS):
            self.assertEqual(next_step(blocking, round), "question")
        self.assertEqual(next_step(blocking, MAX_ROUNDS), "proposal")
        self.assertEqual(next_step([gap("GAP-001", ["REQ-001"], blocking=False)], 0), "review")


class ApplyUpdateTest(unittest.TestCase):
    def test_case_b_adds_and_keeps_confirmed(self):
        reqs = [req("REQ-001"), req("REQ-002")]
        result = apply_update(reqs, [updated("REQ-003")], PID)
        self.assertEqual([r.id for r in result], ["REQ-001", "REQ-002", "REQ-003"])
        self.assertEqual(result[:2], reqs)

    def test_case_a_replaces_in_place(self):
        reqs = [req("REQ-001"), req("REQ-002", status="needs_clarification", blocking=True), req("REQ-003")]
        result = apply_update(reqs, [updated("REQ-002")], PID)
        self.assertEqual([r.id for r in result], ["REQ-001", "REQ-002", "REQ-003"])
        self.assertEqual(result[1].status, "confirmed")
        self.assertEqual(result[1].source, "clarification_answer")
        self.assertFalse(result[1].blocking)
        self.assertEqual([result[0], result[2]], [reqs[0], reqs[2]])

    def test_rejects_invalid_updates(self):
        reqs = [req("REQ-001"), req("REQ-002", status="proposed", source="ai_proposal"),
                req("REQ-004", source="review_input")]
        for updates in ([updated("REQ-002")], [updated("REQ-003", project_id="other")],
                        [updated("REQ-003"), updated("REQ-003")],
                        [updated("REQ-001")], [updated("REQ-004")]):  # 최초 입력·Review 입력 출처 보존
            with self.subTest(updates=[u.id for u in updates]), self.assertRaises(ValueError):
                apply_update(reqs, updates, PID)

    def test_replaces_previous_clarification_answer(self):
        reqs = [req("REQ-001"), req("REQ-002", source="clarification_answer")]
        result = apply_update(reqs, [updated("REQ-002")], PID)
        self.assertEqual(result[1].description, "REQ-002 답변")


class ToProposalsTest(unittest.TestCase):
    def test_case_b_handoff_adds_new_proposals(self):
        state = handoff_state()
        blocking = [g for g in state.gaps if g.blocking]
        result = to_proposals(state.requirements, blocking, [question(g.id) for g in blocking], PID)
        self.assertEqual(result[:5], state.requirements)
        new = result[5:]
        self.assertEqual([r.id for r in new], ["REQ-006", "REQ-007", "REQ-008", "REQ-009", "REQ-010"])
        self.assertEqual([r.category for r in new], ["flow", "state", "permission", "exception", "business_rule"])
        for r in new:
            self.assertEqual((r.status, r.source, r.blocking, r.project_id), ("proposed", "ai_proposal", False, PID))
        self.assertEqual(new[0].description, "GAP-001 기본값을 적용한다.")
        self.assertFalse(any(r.source == "ai_proposal" and r.status == "confirmed" for r in result))

    def test_case_a_converts_linked_item_keeping_id(self):
        reqs = [req("REQ-001"), req("REQ-002", status="needs_clarification", blocking=True, category="permission"),
                req("REQ-003", status="needs_clarification", blocking=True)]
        result = to_proposals(reqs, [gap("GAP-001", ["REQ-001", "REQ-002", "REQ-003"])], [question("GAP-001")], PID)
        self.assertEqual([r.id for r in result], ["REQ-001", "REQ-002", "REQ-003"])
        self.assertEqual(result[0], reqs[0])
        converted = result[1]
        self.assertEqual((converted.status, converted.source, converted.blocking, converted.category),
                         ("proposed", "ai_proposal", False, "permission"))
        self.assertEqual(converted.description, "GAP-001 기본값을 적용한다.")
        self.assertEqual((result[2].status, result[2].blocking), ("needs_clarification", False))

    def test_ignores_nonblocking_and_requires_question(self):
        reqs = [req("REQ-001")]
        self.assertEqual(to_proposals(reqs, [gap("GAP-001", ["REQ-001"], blocking=False)], [], PID), reqs)
        with self.assertRaises(ValueError):
            to_proposals(reqs, [gap("GAP-001", ["REQ-001"])], [], PID)


class AcceptTest(unittest.TestCase):
    def test_single_and_bulk_accept_keep_source(self):
        reqs = proposal_state().requirements
        one = accept(reqs, ["REQ-006"])
        self.assertEqual((one[5].status, one[5].source, one[5].blocking), ("confirmed", "ai_proposal", False))
        self.assertEqual([r.status for r in one[6:]], ["proposed"] * 4)
        bulk = accept(reqs, ["REQ-007", "REQ-009"])
        self.assertEqual([r.status for r in bulk[5:]], ["proposed", "confirmed", "proposed", "confirmed", "proposed"])
        self.assertEqual(len(bulk), len(reqs))

    def test_rejects_non_proposed_or_unknown(self):
        reqs = proposal_state().requirements
        for ids in (["REQ-001"], ["REQ-999"], []):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                accept(reqs, ids)


class EditAddTest(unittest.TestCase):
    def test_edit_updates_content_and_keeps_source(self):
        state = proposal_state()
        result = edit(state, EditAction(type="edit", id="REQ-006", description="수정됨", acceptance_criteria=["AC"]))
        r = result.requirements[5]
        self.assertEqual((r.description, r.acceptance_criteria, r.status, r.source), ("수정됨", ["AC"], "proposed", "ai_proposal"))
        self.assertEqual(result.edit_count, 1)
        self.assertEqual(state.edit_count, 0)

    def test_edit_confirm_clears_blocking_keeps_source(self):
        state = handoff_state(requirements=[req("REQ-001", status="needs_clarification", blocking=True)])
        r = edit(state, EditAction(type="edit", id="REQ-001", category="permission", confirm=True)).requirements[0]
        self.assertEqual((r.status, r.blocking, r.source, r.category), ("confirmed", False, "initial_input", "permission"))

    def test_edit_rejects_unknown_id(self):
        with self.assertRaises(ValueError):
            edit(handoff_state(), EditAction(type="edit", id="REQ-999", description="x"))

    def test_edit_cannot_create_proposed(self):
        with self.assertRaises(ValidationError):
            EditAction(type="edit", id="REQ-001", status="proposed")

    def test_add_creates_confirmed_review_input(self):
        result = add(handoff_state(), AddAction(type="add", category="nfr", description="새 요구"))
        r = result.requirements[-1]
        self.assertEqual((r.id, r.status, r.source, r.blocking, r.project_id),
                         ("REQ-006", "confirmed", "review_input", False, PID))
        self.assertEqual(result.edit_count, 1)

    def test_review_action_discriminator(self):
        action = TypeAdapter(ReviewAction).validate_python({"type": "accept", "ids": ["REQ-006"]})
        self.assertEqual(action.ids, ["REQ-006"])
        resolve = TypeAdapter(ReviewAction).validate_python({"type": "resolve_gap", "gap_id": "GAP-001", "description": "x"})
        self.assertEqual((resolve.gap_id, resolve.category), ("GAP-001", None))
        with self.assertRaises(ValidationError):
            TypeAdapter(ReviewAction).validate_python({"type": "delete", "id": "REQ-001"})


class ApproveTest(unittest.TestCase):
    def test_rejects_unresolved_blocking(self):
        state = handoff_state(phase="review",
                              requirements=[req("REQ-001", status="needs_clarification", blocking=True)])
        with self.assertRaises(ValueError):
            approve(state)

    def test_approves_with_unaccepted_proposals_and_reports_metrics(self):
        state = proposal_state()
        state = state.model_copy(update={
            "requirements": accept(state.requirements, ["REQ-006"]),
            "edit_count": 2,
            "history": [ClarificationRound(round=1, gaps=state.gaps, questions=[question("GAP-001"), question("GAP-002")],
                                           answers=[]),
                        ClarificationRound(round=2, gaps=state.gaps, questions=[question("GAP-003")], answers=[])],
        })
        new_state, reviewed = approve(state)
        self.assertEqual(new_state.phase, "approved")
        self.assertEqual((reviewed.project_id, reviewed.run_id), (PID, HANDOFF["run_id"]))
        self.assertEqual(reviewed.requirements, state.requirements)
        self.assertEqual(sum(r.status == "proposed" for r in reviewed.requirements), 4)
        self.assertEqual(reviewed.metrics, {"question_count": 3, "turn_count": 2, "edit_count": 2,
                                            "unaccepted_proposal_count": 4})


class OpenGapsTest(unittest.TestCase):
    def review_state(self):
        reqs = [req("REQ-001"), req("REQ-002", status="needs_clarification")]
        gaps = [gap("GAP-001", ["REQ-001"], blocking=False),   # 전달
                gap("GAP-002", [], blocking=False),            # 서비스 전반, 전달
                gap("GAP-003", ["REQ-002"], blocking=False),   # 미결정 Requirement가 Open Issue로 가므로 제외
                gap("GAP-004", ["REQ-001"])]                   # blocking은 질문·Proposal로 처리됨, 제외
        return handoff_state(phase="review", requirements=reqs, gaps=gaps)

    def test_approve_hands_off_remaining_nonblocking_gaps(self):
        _, reviewed = approve(self.review_state())
        self.assertEqual([g.id for g in reviewed.open_gaps], ["GAP-001", "GAP-002"])
        self.assertEqual(reviewed.open_gaps[0].related_requirement_ids, ["REQ-001"])
        self.assertEqual(len(reviewed.requirements), 2)  # Gap을 Requirement로 바꾸지 않는다

    def test_confirming_unresolved_requirement_resolves_linked_gap(self):
        state = edit(self.review_state(), EditAction(type="edit", id="REQ-002", confirm=True))
        self.assertEqual([g.id for g in state.gaps], ["GAP-001", "GAP-002", "GAP-004"])
        self.assertEqual([g.id for g in approve(state)[1].open_gaps], ["GAP-001", "GAP-002"])

    def test_confirming_already_confirmed_keeps_gaps(self):
        state = edit(self.review_state(), EditAction(type="edit", id="REQ-001", description="x", confirm=True))
        self.assertEqual(len(state.gaps), 4)

    def test_resolve_gap_records_user_decision_and_removes_gap(self):
        state = resolve_gap(self.review_state(), ResolveGapAction(type="resolve_gap", gap_id="GAP-001",
                                                                  description="중복 신청은 거부한다."))
        new = state.requirements[-1]
        self.assertEqual((new.id, new.category, new.description, new.status, new.source, new.blocking),
                         ("REQ-003", "business_rule", "중복 신청은 거부한다.", "confirmed", "review_input", False))
        self.assertEqual(state.edit_count, 1)
        self.assertEqual([g.id for g in approve(state)[1].open_gaps], ["GAP-002"])

    def test_resolve_gap_uses_given_category(self):
        state = resolve_gap(self.review_state(), ResolveGapAction(type="resolve_gap", gap_id="GAP-002",
                                                                  description="범위에서 제외한다.", category="scope"))
        self.assertEqual(state.requirements[-1].category, "scope")

    def test_resolve_gap_rejects_non_handoff_gap_or_blank_decision(self):
        for gap_id, description in (("GAP-003", "x"), ("GAP-004", "x"), ("GAP-999", "x"), ("GAP-001", " ")):
            with self.subTest(gap_id=gap_id), self.assertRaises(ValueError):
                resolve_gap(self.review_state(), ResolveGapAction(type="resolve_gap", gap_id=gap_id,
                                                                  description=description))

    def test_handoff_without_open_gaps_field_is_valid(self):
        old = {"project_id": PID, "run_id": "run", "requirements": [req("REQ-001").model_dump()], "metrics": {}}
        self.assertEqual(ReviewedRequirements.model_validate(old).open_gaps, [])


class PhaseGuardTest(unittest.TestCase):
    def test_require_phase(self):
        require_phase(handoff_state(), "clarifying")
        for phase, expected in (("clarifying", "review"), ("review", "clarifying"),
                                ("approved", "review"), ("approved", "clarifying")):
            with self.subTest(phase=phase, expected=expected), self.assertRaises(ValueError):
                require_phase(handoff_state(phase=phase), expected)


if __name__ == "__main__":
    unittest.main()

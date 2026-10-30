"""Single-round policy regressions. All model outputs are fixtures; no paid calls."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from app import pipeline
from app.llm.errors import AnalysisError
from app.llm.mock import MockProvider
from app.m2 import stages, web
from app.m2.contracts import PRD, ReviewedPRD, Spec, check_reviewed_prd, check_spec
from app.schemas import Answer, EditAction, Gap, WorkflowState
from app.workflow import approve, validate_state


def state(text='사용자는 기록을 조회할 수 있다.'):
    result = pipeline.analyze(text, MockProvider(), policy='single_round_v1')
    return WorkflowState(**result.model_dump(), text=text)


class RecordingMock(MockProvider):
    def __init__(self, mutate=None):
        self.calls = []
        self.mutate = mutate

    def complete(self, stage, prompt, payload, output_type, model):
        self.calls.append((stage, copy.deepcopy(payload)))
        raw, usage = super().complete(stage, prompt, payload, output_type, model)
        if self.mutate and stage == 'm2_review_completion':
            data = json.loads(raw); self.mutate(data)
            raw = json.dumps(data, ensure_ascii=False)
        return raw, usage


class SingleRoundTest(unittest.TestCase):
    def test_zero_questions_reaches_review_with_a_single_role(self):
        provider = RecordingMock()
        result = pipeline.clarify_step(state(), provider=provider)
        validate_state(result)
        self.assertEqual((result.phase, result.clarification_round, result.questions), ('review', 0, []))
        self.assertIsNotNone(result.selected_role_id)
        _, reviewed = approve(result)
        self.assertEqual(reviewed.policy, 'single_round_v1')
        self.assertEqual([c[0] for c in provider.calls], ['m2_clarification_plan', 'm2_review_completion'])

    def test_unlinked_and_nonblocking_gaps_and_overflow_are_not_dropped(self):
        initial = state().model_dump()
        initial['gaps'] = [dict(id=f'G-{i}', category='core_flow', description=f'미정 {i}',
                               blocking=i < 7, related_requirement_ids=[]) for i in range(8)]
        asked = pipeline.clarify_step(WorkflowState.model_validate(initial), provider=MockProvider())
        self.assertEqual(len(asked.questions), 5)
        done = pipeline.clarify_step(asked, [], provider=MockProvider())
        validate_state(done)
        self.assertEqual((done.phase, done.clarification_round, len(done.history)), ('review', 1, 1))
        self.assertEqual({c.gap.id for c in done.proposal_conversions}, {f'G-{i}' for i in range(8)})
        self.assertEqual([c.reason for c in done.proposal_conversions].count('question_limit'), 2)
        self.assertEqual([c.reason for c in done.proposal_conversions].count('ai_default'), 1)
        self.assertTrue(all(r.status == 'proposed' for r in done.requirements if r.source == 'ai_proposal'))
        self.assertEqual(approve(done)[1].metrics['forced_proposal_count'], 7)
        with self.assertRaises(ValueError):
            pipeline.clarify_step(done, [], provider=MockProvider())

    def test_accept_reject_and_authored_answers_have_distinct_provenance(self):
        initial = state('조회 정책은 미정. 보안 정책은 미정.')
        # Use three independent blockers, one linked and two service-wide.
        initial.gaps = initial.gaps[:1]
        base = initial.gaps[0]
        initial.gaps += [base.model_copy(update={'id': f'G-{i}', 'related_requirement_ids': [], 'blocking': True}) for i in (2, 3)]
        initial.requirements = [r for r in initial.requirements if r.id in base.related_requirement_ids]
        asked = pipeline.clarify_step(initial, provider=MockProvider())
        self.assertEqual(len(asked.questions), 3)
        q1,q2,q3 = asked.questions
        provider = RecordingMock()
        done = pipeline.clarify_step(asked, [Answer(gap_id=q1.gap_id, answer='forged', decision='accept'),
            Answer(gap_id=q2.gap_id, answer='자동 확정하지 않고 임시 저장한다', decision='reject'),
            Answer(gap_id=q3.gap_id, answer='작성자만 변경할 수 있다', decision='answer')], provider=provider)
        accepted = next(r for r in done.requirements if r.description == q1.ai_proposal)
        self.assertEqual((accepted.status, accepted.source), ('confirmed', 'ai_proposal'))
        self.assertEqual(sum(r.source == 'clarification_answer' for r in done.requirements), 2)
        self.assertEqual(provider.calls[0][1]['history'][0]['questions'][0]['ai_proposal'], q1.ai_proposal)
        self.assertEqual(done.history[0].answers[0].answer, q1.ai_proposal)
        validate_state(done)
        self.assertEqual(approve(done)[1].metrics['forced_proposal_count'], 0)

    def test_six_questions_or_missing_resolution_is_rejected_not_truncated(self):
        initial = state()
        initial.gaps = [Gap(id=f'G-{i}', category='core_flow',
            description=f'결정 {i}', blocking=True, related_requirement_ids=[]) for i in range(6)]
        from app.m2 import requirements_mock
        plan = requirements_mock.plan(initial.model_dump())
        plan['questions'].append(dict(gap_id='G-5',question='중요한 결정?',ai_proposal='기본값'))
        with patch.object(requirements_mock, 'plan', return_value=plan), self.assertRaises(AnalysisError):
            pipeline.clarify_step(initial, provider=MockProvider())
        asked = pipeline.clarify_step(initial, provider=MockProvider())
        with self.assertRaises(AnalysisError):
            pipeline.clarify_step(asked, [], RecordingMock(lambda d: d['resolutions'].pop()))

    def test_rejected_recommendation_cannot_return_and_confirmed_input_is_immutable(self):
        asked = pipeline.clarify_step(state('신청 가능 여부는 미정.'), provider=MockProvider())
        q = asked.questions[0]
        def restore_rejected(d):
            rid = d['resolutions'][0]['requirement_id']
            next(r for r in d['requirements'] if r['id'] == rid)['description'] = q.ai_proposal
        with self.assertRaises(AnalysisError):
            pipeline.clarify_step(asked, [Answer(gap_id=q.gap_id, answer='다른 방법을 사용한다',decision='reject')],
                                  RecordingMock(restore_rejected))
        with self.assertRaises(AnalysisError):
            pipeline.clarify_step(state(), provider=RecordingMock(lambda d: d['requirements'][0].update(description='변조')))

    def test_reject_without_alternative_stops_before_model(self):
        asked = pipeline.clarify_step(state('신청 가능 여부는 미정.'), provider=MockProvider())
        provider = RecordingMock()
        with self.assertRaises(ValueError):
            pipeline.clarify_step(asked, [Answer(gap_id=asked.questions[0].gap_id, answer=' ', decision='reject')], provider)
        self.assertFalse(provider.calls)

    def test_multiple_roles_share_the_single_question_round(self):
        asked = pipeline.clarify_step(state('직원이 신청하고 관리자가 승인한다.'), provider=MockProvider())
        role_question = next(q for q in asked.questions if q.gap_id.startswith('GAP-ROLE'))
        done = pipeline.clarify_step(asked, [Answer(gap_id=role_question.gap_id, answer='관리자 역할로 사용한다', decision='answer')], MockProvider())
        chosen = next(r for r in done.requirements if r.id == done.selected_role_id)
        self.assertEqual(chosen.description, '관리자 역할로 사용한다')
        self.assertTrue(all(r.id in done.out_of_scope_ids for r in done.requirements if r.category == 'role' and r.id != chosen.id))
        validate_state(done)

    def test_model_cannot_drop_original_or_confirm_its_own_default(self):
        asked = pipeline.clarify_step(state('신청 가능 여부는 미정.'), provider=MockProvider())
        for mutate in (lambda d: d['requirements'].pop(0),
                       lambda d: d['requirements'][0].update(status='confirmed',source='initial_input')):
            with self.subTest(mutate=mutate), self.assertRaises(AnalysisError):
                pipeline.clarify_step(asked, [], RecordingMock(mutate))

    def test_review_role_edit_recomputes_scope_without_reasking(self):
        done = pipeline.clarify_step(state(), provider=MockProvider())
        provider = RecordingMock()
        with patch.object(pipeline, 'get_provider', return_value=provider):
            changed, _ = pipeline.review_step(done, EditAction(type='edit', id=done.selected_role_id,
                description='운영자 한 명', confirm=True))
        role = next(r for r in changed.requirements if r.id == changed.selected_role_id)
        self.assertEqual((role.description, role.source, role.status), ('운영자 한 명','review_input','confirmed'))
        self.assertEqual(changed.phase, 'review')
        self.assertFalse(changed.questions)
        validate_state(changed)

    def test_invalid_review_edit_is_rejected_before_model_call(self):
        done = pipeline.clarify_step(state('기록을 조회한다.'), provider=MockProvider())
        with patch.object(pipeline, 'get_provider') as provider, self.assertRaises(ValueError):
            pipeline.review_step(done, EditAction(type='edit',id=done.selected_role_id,
                                                description='사용자 입력을 미확정 AI 제안으로 저장',confirm=False))
        provider.assert_not_called()

    def test_legacy_hash_keeps_pre_policy_serialization(self):
        from test_m2_web import approved_state
        old = approved_state().model_dump(exclude={'policy', 'selected_role_id', 'out_of_scope_ids', 'proposal_conversions'})
        for h in old['history']:
            for a in h['answers']: a.pop('decision')
        expected = hashlib.sha256(json.dumps(old, ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:16]
        restored = WorkflowState.model_validate(old)
        self.assertEqual(web.prd_id_of(restored), expected)

    def test_new_prd_rejects_reopened_decisions_or_multiple_roles(self):
        from test_m2_web import prd_output, approved_state
        reviewed = approve(approved_state())[1]
        prd = ReviewedPRD.model_validate(prd_output())
        reviewed.policy='single_round_v1'; reviewed.selected_role_id='REQ-001'
        prd.policy='single_round_v1'
        self.assertIn('single-round PRD cannot reopen decisions', check_reviewed_prd(prd, reviewed))
        prd.roles.append(prd.roles[0].model_copy(update={'id':'ROLE-002'}))
        self.assertIn('single-role PRD requires exactly one role', check_reviewed_prd(prd, reviewed))

    def test_excluded_scope_and_proposal_provenance_cannot_be_overwritten_by_prd(self):
        from test_m2_web import prd_output, approved_state
        reviewed = approve(approved_state())[1]
        reviewed.policy='single_round_v1'; reviewed.selected_role_id='REQ-001'
        reviewed.out_of_scope_ids=['REQ-004']
        prd = ReviewedPRD.model_validate({**prd_output(), 'policy':'single_round_v1'})
        prd.roles[0].description = reviewed.requirements[0].description
        prd.roles[0].requirement_ids = []
        prd.functional_requirements[3].source = 'harness_policy'
        prd.functional_requirements[3].status = 'confirmed'
        errors = check_reviewed_prd(prd,reviewed)
        self.assertIn('PRD role must reference the selected requirement',errors)
        self.assertIn('FR-003: excluded requirement returned to implementation',errors)
        self.assertIn('FR-004: reviewed status and source must be preserved',errors)

    def test_single_role_policy_reaches_spec_plan_and_context(self):
        import tempfile
        from pathlib import Path
        from test_m2 import prd_data, spec_data, plan_data
        from app.m2.context import build_context
        from app.m2.contracts import Plan
        prd = PRD.model_validate({**prd_data(), 'policy':'single_round_v1'})
        spec = Spec.model_validate(spec_data())
        self.assertEqual(check_spec(spec, prd), [])
        spec.checks[0].http_steps[0].role_header = 'admin'
        self.assertIn('single-role checks must not switch X-Role', check_spec(spec, prd))
        spec.checks[0].http_steps[0].role_header = ''
        with patch.object(stages, 'generate', return_value=spec) as generation:
            stages.generate_spec(prd, [])
            self.assertIn('single_round_v1', generation.call_args.args[2])
            self.assertIn('Exactly one primary', generation.call_args.args[3]['stack_contract'])
            stages.generate_plan(prd, spec, [])
            self.assertIn('Exactly one primary', generation.call_args.args[3]['stack_contract'])
        plan = Plan.model_validate(plan_data())
        with tempfile.TemporaryDirectory() as tmp, patch('app.m2.context.git', return_value=''):
            ctx = build_context(plan.tasks[1], prd, spec, Path(tmp), {}, 'offline')
        self.assertIn('Exactly one primary', ctx['environment']['stack_contract'])
        self.assertEqual(ctx['environment']['roles'], [r.model_dump() for r in prd.roles])


if __name__ == '__main__': unittest.main()

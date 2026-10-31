"""Deterministic offline fixtures, NOT the product's content-generation logic."""
from app.llm.mock import generate_questions
from app.schemas import Gap, Requirement
from app.workflow import GAP_TO_REQUIREMENT_CATEGORY, next_requirement_id


def plan(payload):
    roles = [r for r in payload['requirements'] if r['category'] == 'role']
    gaps = [Gap.model_validate(g) for g in payload['gaps']]
    role_gap = None
    questions = [q.model_dump() for q in generate_questions(gaps)]
    if len(roles) > 1:
        ids = {g.id for g in gaps}
        i = 1
        while f'GAP-ROLE-{i}' in ids:
            i += 1
        role_gap = dict(id=f'GAP-ROLE-{i}', category='user_role', description='이번 데모에서 사용할 주 역할',
                        blocking=True, related_requirement_ids=[r['id'] for r in roles])
        questions = [dict(gap_id=role_gap['id'], question='이번 데모는 어떤 역할로 사용할까요?',
                          ai_proposal=roles[0]['description'])] + questions
    return dict(role_gap=role_gap, questions=questions[:5])


def complete_review(payload):
    reqs = [Requirement.model_validate(r) for r in payload['requirements']]
    resolutions = []
    answers = {a['gap_id']: a for h in payload['history'] for a in h['answers']}
    questions = {q['gap_id']: q for h in payload['history'] for q in h['questions']}
    chosen = payload.get('role_override')
    for raw in payload['gaps']:
        gap = Gap.model_validate(raw)
        answer = answers.get(gap.id, {'decision': 'skip', 'answer': ''})
        accepted = answer['decision'] == 'accept'
        authored = answer['decision'] in ('answer', 'reject')
        default = questions.get(gap.id, {}).get('ai_proposal', f'{gap.description}: 일반적인 데모 기본값을 사용한다.')
        description = answer['answer'] if authored else default
        updates = dict(description=description, status='confirmed' if accepted or authored else 'proposed',
                       source='clarification_answer' if authored else 'ai_proposal', blocking=False)
        linked = [r for r in reqs if r.id in gap.related_requirement_ids and r.status == 'needs_clarification']
        if linked:
            targets = [r.id for r in linked]
            reqs = [r.model_copy(update=updates) if r.id in targets else r for r in reqs]
            rid = targets[0]
        else:
            rid = next_requirement_id(reqs)
            reqs.append(Requirement(project_id=payload['project_id'], id=rid,
                                    category=GAP_TO_REQUIREMENT_CATEGORY[gap.category], **updates))
        resolutions.append(dict(gap_id=gap.id, requirement_id=rid))
        if gap.category == 'user_role' and not chosen:
            chosen = rid
    roles = [r for r in reqs if r.category == 'role']
    if not chosen and roles:
        chosen = roles[0].id
    if not chosen:
        chosen = next_requirement_id(reqs)
        reqs.append(Requirement(project_id=payload['project_id'], id=chosen, category='role',
                                description='서비스 이용자', status='proposed', source='ai_proposal', blocking=False))
    return dict(requirements=[r.model_dump() for r in reqs], resolutions=resolutions,
                selected_role_id=chosen, out_of_scope_ids=[r.id for r in reqs if r.category == 'role' and r.id != chosen])

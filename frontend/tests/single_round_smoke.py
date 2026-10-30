"""Offline browser check for single-round questions and review. Build frontend first.
All network requests are intercepted; no backend or paid API is used.
"""
import copy
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

DIST = Path(__file__).resolve().parents[1] / 'dist'
RUN = 'web-1111111111111111'
requests, unexpected, errors = [], [], []

def req(id, description, category='functional', status='confirmed', source='initial_input'):
    return dict(project_id='test', id=id, description=description, category=category,
                status=status, source=source, blocking=False, acceptance_criteria=[])

def gap(id):
    return dict(id=id, description='중요한 결정', category='core_flow',blocking=True,related_requirement_ids=[])

questions = [dict(gap_id='G-1', question='예약은 어떻게 확정할까요?', ai_proposal='예약은 즉시 확정한다'),
             dict(gap_id='G-2', question='취소는 어떻게 할까요?', ai_proposal='언제나 취소할 수 있다')]
analysis = dict(project_id='test',run_id='analysis',policy='single_round_v1',clarification_round=0,
                requirements=[req('R-0','처음 입력한 기능')],gaps=[gap('G-1'),gap('G-2'),gap('G-3')],
                clarification_needed=True,analysis_mode='mock',usage=[])
reviewed = dict(**{**analysis, 'gaps':[], 'clarification_needed':False,'clarification_round':1},
    text='예약 서비스',phase='review',questions=[],edit_count=0,
    history=[dict(round=1,questions=questions,gaps=analysis['gaps'],answers=[])],
    selected_role_id='R-role',out_of_scope_ids=['R-other'],gap_links=[],
    proposal_conversions=[dict(gap=gap('G-3'),requirement_id='R-ai',reason='question_limit',round=1)])
reviewed['requirements'] += [req('R-role','예약 이용자','role'), req('R-other','운영자','role'),
    req('R-ai','하루에 한 번 예약한다',status='proposed',source='ai_proposal'),
    req('R-detail','목록은 최근 항목부터 보여준다',status='proposed',source='ai_proposal')]


def route_request(route):
    path = urlparse(route.request.url).path
    if path.startswith('/api/'):
        data = route.request.post_data_json
        requests.append((path, data))
        if path == '/api/requirements/analyze':
            assert data['policy'] == 'single_round_v1'
            body = analysis
        elif path == '/api/clarification/step':
            if 'answers' not in data:
                body = {**data['state'],'clarification_round':1,'questions':questions}
            else:
                assert data['answers'] == [dict(gap_id='G-1',answer='',decision='accept'),
                                          dict(gap_id='G-2',answer='하루 전까지 취소한다',decision='reject')]
                body = copy.deepcopy(reviewed)
                body['history'][0]['answers'] = data['answers']
        elif path == '/api/review':
            body = copy.deepcopy(data['state'])
            action = data['action']
            assert action['type'] == 'edit'
            r = next(r for r in body['requirements'] if r['id'] == action['id'])
            r.update(description=action['description'],source='review_input',status='confirmed')
            body = dict(state=body)
        elif path == '/api/m2/generate':
            body = dict(run_id=RUN,created=True)
        elif path == f'/api/m2/runs/{RUN}':
            body = dict(run_id=RUN,prd_id=RUN[4:],outcome='running',current_stage='prd',reason=None,
                        tasks=[],task_order=[],preview_url=None,open_issues=[],requires_cost_review=False)
        else:
            unexpected.append(path); route.abort(); return
        route.fulfill(content_type='application/json',body=json.dumps(body))
    else:
        target = DIST / (path.lstrip('/') or 'index.html')
        if target.is_file() and target.resolve().is_relative_to(DIST.resolve()):
            route.fulfill(content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream',body=target.read_bytes())
        else:
            unexpected.append(path); route.abort()

with sync_playwright() as p:
    options = dict(headless=True)
    if os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'):
        options['executable_path'] = os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']
    browser = p.chromium.launch(**options)
    page = browser.new_page(viewport=dict(width=1100,height=900))
    page.route('**/*',route_request)
    page.on('pageerror',lambda e: errors.append(str(e)))
    page.goto('http://harness.test/')
    page.get_by_role('textbox',name='서비스 설명').fill('예약 서비스')
    page.get_by_role('button',name='시작하기',exact=True).click()
    expect(page.locator('.question')).to_have_count(2)
    page.get_by_role('button',name='추천안 모두 수락').click()
    expect(page.locator('.question button[aria-pressed="true"]')).to_have_count(2)
    page.get_by_role('button',name='추천안 모두 거절').click()
    before = len(requests)
    page.get_by_role('button',name='답변 제출').click()
    assert len(requests) == before, 'A rejection without an alternative must not call the API'
    page.locator('.question').nth(0).get_by_role('button',name='추천안 수락',exact=True).click()
    page.locator('.question textarea').nth(1).fill('하루 전까지 취소한다')
    page.get_by_role('button',name='답변 제출').click()
    expect(page.get_by_role('heading',name='중요한 AI 결정사항')).to_be_visible()
    expect(page.get_by_text('처음 입력한 기능',exact=True)).to_have_count(0)
    page.get_by_text('이번 데모에서 제외되는 역할과 기능',exact=True).click()
    expect(page.get_by_text('운영자',exact=True)).to_be_visible()
    decision = page.locator('li').filter(has_text='하루에 한 번 예약한다')
    decision.get_by_role('button',name='수정',exact=True).click()
    page.get_by_role('textbox',name='수정할 내용').fill('하루에 두 번 예약한다')
    page.get_by_role('button',name='저장',exact=True).click()
    expect(page.locator('li').filter(has_text='하루에 두 번 예약한다')).to_have_count(1)
    page.get_by_role('button',name='역할 변경',exact=True).click()
    page.get_by_role('textbox',name='수정할 내용').fill('개인 예약 이용자')
    page.get_by_role('button',name='역할과 범위 변경',exact=True).click()
    expect(page.get_by_text('개인 예약 이용자',exact=True)).to_be_visible()
    page.get_by_role('button',name='생성',exact=True).click()
    expect(page.get_by_role('heading',name='서비스를 만들고 있습니다')).to_be_visible()
    payload = next(data['state'] for path,data in requests if path == '/api/m2/generate')
    assert payload['selected_role_id'] == 'R-role' and payload['policy'] == 'single_round_v1'
    assert next(r for r in payload['requirements'] if r['id']=='R-detail')['status']=='proposed'
    assert next(r for r in payload['requirements'] if r['id']=='R-ai')['source']=='review_input'
    assert not unexpected and not errors, (unexpected,errors)
    browser.close()
print('PASS: bounded questions, bulk accept/reject, alternative required, AI decisions, role edit, generate without auto-accept')

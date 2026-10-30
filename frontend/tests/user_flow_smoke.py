"""Offline browser regression: build frontend first, then run with Python Playwright + Chromium.
All HTTP requests are fulfilled locally; no backend, Docker, or paid API is used.
Optional: set PLAYWRIGHT_CHROMIUM_EXECUTABLE to an existing Chromium binary.
"""
import json
import copy
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

DIST = Path(__file__).resolve().parents[1] / 'dist'
assert (DIST / 'index.html').exists(), 'Run npm run build first'
RUN = 'web-0123456789abcdef'
status = dict(run_id=RUN, prd_id=RUN[4:], outcome='running', reason=None,
              requires_cost_review=False, current_stage='spec', tasks=[], task_order=[],
              preview_url=None, open_issues=[])
requests = []
unexpected = []
state = dict(project_id='test', run_id='analysis', clarification_round=0,
             requirements=[dict(project_id='test', id='REQ-001', category='scope', description='예약 기간 미정',
                                status='needs_clarification', source='initial_input', blocking=False,
                                acceptance_criteria=[])],
             gaps=[], clarification_needed=False, analysis_mode='mock', usage=[],
             text='모임 관리 서비스', phase='review', questions=[], history=[], edit_count=0, gap_links=[])


def route_request(route):
    path = urlparse(route.request.url).path
    if path.startswith('/api/'):
        requests.append((route.request.method, path, route.request.post_data_json))
        if path == '/api/requirements/analyze':
            body = state
        elif path == '/api/clarification/step':
            body = state
        elif path == '/api/review':
            payload = route.request.post_data_json
            updated = copy.deepcopy(payload['state'])
            action = payload['action']
            assert action['type'] == 'edit'
            for item in updated['requirements']:
                if item['id'] == action['id']:
                    item.update(description=action['description'], status='confirmed', blocking=False)
            body = {'state': updated}
        elif path == '/api/m2/generate':
            body = dict(run_id=RUN, created=True)
        elif path == f'/api/m2/runs/{RUN}':
            body = status
        elif path == '/api/m2/runs':
            status.update(outcome='running', current_stage='build', reason=None)
            body = dict(run_id=RUN, created=False)
        else:
            unexpected.append(path)
            route.abort()
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(body))
    elif path == '/preview':
        route.fulfill(content_type='text/html', body='<h1>예시 앱</h1>')
    else:
        target = DIST / (path.lstrip('/') or 'index.html')
        if target.is_file() and target.resolve().is_relative_to(DIST.resolve()):
            route.fulfill(content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream', body=target.read_bytes())
        else:
            unexpected.append(path)
            route.abort()


with sync_playwright() as p:
    options = {'headless': True}
    if os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'):
        options['executable_path'] = os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']
    browser = p.chromium.launch(**options)
    context = browser.new_context()
    context.route('**/*', route_request)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://harness.test/')
    page.get_by_role('textbox', name='서비스 설명').fill('모임 관리 서비스')
    page.get_by_role('button', name='시작하기', exact=True).click()
    page.get_by_role('button', name='정하기', exact=True).click()
    page.locator('form.editor textarea').fill('일주일 전부터 예약')
    page.get_by_role('button', name='정하기', exact=True).click()
    decision = page.locator('li').filter(has_text='일주일 전부터 예약')
    expect(decision).to_have_count(1)
    decision.get_by_role('button', name='수정', exact=True).click()
    page.locator('form.editor textarea').fill('이틀 전부터 예약')
    page.get_by_role('button', name='저장', exact=True).click()
    expect(page.locator('li').filter(has_text='이틀 전부터 예약')).to_have_count(1)
    page.get_by_role('button', name='생성', exact=True).click()
    expect(page.get_by_role('heading', name='서비스를 만들고 있습니다')).to_be_visible()
    expect(page.locator('[aria-current="step"]')).to_have_text('생성')
    expect(page.get_by_role('button', name='Harness', exact=True)).to_be_disabled()
    generated = next(body for _, path, body in requests if path == '/api/m2/generate')
    assert generated['state']['requirements'][0]['source'] == 'initial_input'
    assert generated['state']['requirements'][0]['description'] == '이틀 전부터 예약'
    page.reload()
    expect(page.get_by_role('heading', name='서비스를 만들고 있습니다')).to_be_visible()
    expect(page.get_by_role('button', name='Harness', exact=True)).to_be_disabled()
    assert page.evaluate("localStorage.getItem('harness.currentRun')") == RUN
    assert sum(path == '/api/m2/generate' for _, path, _ in requests) == 1

    status.update(outcome='auto_checks_passed', preview_url='http://harness.test/preview')
    page.reload()
    expect(page.get_by_role('heading', name='생성이 완료되었습니다!')).to_be_visible()
    expect(page.locator('[aria-current="step"]')).to_have_text('완료')
    expect(page.frame_locator('iframe').get_by_role('heading', name='예시 앱')).to_be_visible()
    expect(page.get_by_role('link', name='새 창에서 열기')).to_have_attribute('href', status['preview_url'])
    assert sum(path == '/api/m2/generate' for _, path, _ in requests) == 1

    status.update(outcome='manual_check_only')
    page.reload()
    expect(page.get_by_role('heading', name='생성이 끝났습니다')).to_be_visible()
    expect(page.get_by_text('자동으로 확인할 항목이 없었습니다.', exact=False)).to_be_visible()

    status.update(outcome='failed', requires_cost_review=True, reason='중단된 AI 호출의 비용 확인이 필요합니다.')
    page.reload()
    expect(page.get_by_role('button', name='비용 확인 상태 새로고침')).to_be_visible()
    expect(page.get_by_role('button', name='이어서 다시 시도')).to_have_count(0)
    status.update(requires_cost_review=False)
    page.get_by_role('button', name='비용 확인 상태 새로고침').click()
    expect(page.get_by_role('button', name='이어서 다시 시도')).to_be_visible()

    status.update(outcome='verification_failed', reason='자동 점검 실패')
    page.reload()
    expect(page.get_by_text('직전 데이터 한 벌만', exact=False)).to_be_visible()
    page.get_by_role('button', name='빌드와 점검 다시 하기').click()
    expect(page.get_by_role('heading', name='서비스를 만들고 있습니다')).to_be_visible()
    assert requests[-2][2] == {'prd_id': RUN[4:], 'retry': True}
    expect(page.locator('[aria-current="step"]')).to_have_text('생성')
    expect(page.get_by_role('button', name='Harness', exact=True)).to_be_disabled()

    status.update(outcome='awaiting_confirmation', open_issues=['예약 취소 정책'])
    page.reload()
    page.get_by_role('button', name='이대로 계속 생성').click()
    expect(page.get_by_role('heading', name='서비스를 만들고 있습니다')).to_be_visible()
    assert any(body == {'prd_id': RUN[4:], 'confirm_open_issues': True} for _, _, body in requests)

    status.update(outcome='auto_checks_passed', preview_url=None)
    page.reload()
    expect(page.get_by_text('미리보기가 꺼져 있습니다.', exact=False)).to_be_visible()
    page.get_by_role('button', name='홈으로', exact=True).click()
    expect(page.get_by_role('heading', name='어떤 서비스를 만들까요?')).to_be_visible()
    assert page.evaluate("localStorage.getItem('harness.currentRun')") is None
    assert not errors, errors
    assert not unexpected, unexpected
    browser.close()
print('PASS: input → review → generate, refresh/completion, manual-only, cost lock/recovery, retry, confirmation, home')

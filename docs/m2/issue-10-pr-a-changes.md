# #10 PR A 변경 기록

실행 증거로 최종 판정을 내리는 부분의 변경 내역이다. 비교 기준은 `origin/feat/m2-user-flow`(#24, `26a003c`)이다. 계약 전문은 [e2e-contract.md 7절](e2e-contract.md#7-최종-판정-10)에 있다.

유료 호출 코드는 없다. Opus 진단, `check_diagnosis`, `run diagnose`는 PR B 범위다.

## 변경 파일

| 파일 | 줄 수 | 변경 |
| --- | --- | --- |
| `backend/app/m2/acceptance.py` | +288 (신규) | `final_status`, 점검·AC·Task별 상태, `gates`, `handoff` 계산. 파일이나 모델을 다루지 않는 순수 함수 |
| `backend/app/m2/contracts.py` | +59 | `ManualReviewEntry`, `ManualReviewSubmission`, `ManualReviewItem`, `ManualReview`, `check_manual_entries`, `check_manual_submission` |
| `backend/app/m2/run.py` | +134 | `judge_run`, `save_verdict`, `retire_verification`, `submit_manual_review`, `run judge`, `run manual-review`, 검증 뒤 판정 저장 |
| `backend/app/m2/web.py` | +65 | `verdict_of`, `manual_review_of`, `record_manual_review`, `run_status`와 `reason_for` 수정 |
| `backend/app/main.py` | +6 | `POST /api/m2/runs/{run_id}/manual-review` |
| `backend/tests/test_m2_acceptance.py` | +544 (신규) | 신규 테스트 42개 |
| `backend/tests/test_m2_web.py` | +9 | #24 테스트 1건의 픽스처 수정(아래 참고) |
| `docs/m2/e2e-contract.md` | +71 | 7절 "최종 판정" 추가, 기존 "실행"은 8절로 이동 |
| `docs/m2/review-runtime.md` | +17 | `final_status`와 `outcome` 대응표, API, 한계 |
| `docs/m2/fixtures/leave-management/acceptance-checklist.md` | 1줄 | 수동 판정 기록 위치 변경 |

`verify.py`, `agent.py`, `claude.py`, 프런트엔드는 변경하지 않았다.

## 달라지는 동작

| 항목 | 이전 | 이후 |
| --- | --- | --- |
| 수동 확인 완료 | `manual-review.json`이 있으면 `done` | 계약을 통과하고 모든 단계가 `pass`여야 `done` |
| 자동 전용 통과의 `manual_review` | `pending` | `not_required` |
| 수동 fail의 `outcome` | 구분 없음 | `verification_failed` |
| 재검증 시작 | 이전 `verify.json` 유지 | `verification.json` 삭제, `verify.json`은 `verify.previous.json`으로 이동 |
| `verify.json` | 코드 정보 없음 | `code_commit`, `spec_sha256` 추가 |
| 손상된 `verify.json` 조회 | 오류 응답 | `insufficient_evidence` |
| 판정 없이 다시 판정 | 불가 | `run judge`(Docker·모델 호출 없음) |

`run_status`에 `can_retry`를 추가했다. `manual_failed`도 `outcome`은 `verification_failed`지만 서버가 재시도를 실행하지 않으므로 `false`다. 화면의 재시도 버튼은 아직 이 값을 쓰지 않는다.

`outcome`의 값 종류는 늘리지 않았다. 환경 오류와 앱 실패는 `final_status`로 구분하며, 화면 구분은 #13에서 한다.

## 결정 사항

| 항목 | 결정 |
| --- | --- |
| `start_failed` | `insufficient_evidence`. `build_failed`는 별도 상태 유지 |
| 빌드 `timeout`, `killed` | `build_failed` + `cause_ambiguous: true` |
| `test_failed` | 분기와 단위 테스트만 있음. **실제 실행 경로 없음** |
| 같은 커밋 재검증 | 수동 pass 유지. 재검증이 데이터를 시드로 되돌리는 한계는 계약 문서에 기록 |
| 수동 제출 | 부분 제출 허용, 이력은 `manual-review.history.jsonl` |
| 자동 전용 `passed` | 인정하고 `check_authorship: "spec_model"` 기록. 더 엄격한 기준은 미결정 |

PR B에서 구현할 결정: `build_failed`, `auto_failed`에서만 자동 진단, 진단 전용 `max_tokens`(예: 16000)로 예약, `manual_failed`는 `diagnosis.status=stale`이고 `run diagnose`로만 유료 호출.

## 계획과 달라진 점

- `reviewer`, `reviewed_at`, `code_commit`, `spec_sha256`을 항목별로도 기록한다. 부분 제출을 합치면 항목마다 작성자와 대상 코드가 다를 수 있기 때문이다. 최상위 `reviewer`, `reviewed_at`은 마지막 제출 값이다.
- 판정 결과는 `run.json`의 `stages`가 아니라 최상위 `final_status`에 둔다. `stages`는 진행 단계 계산에 쓰이기 때문이다.
- `run_status`는 저장된 `verification.json`을 읽지 않고 조회할 때마다 증거에서 다시 판정한다.

## 기존 테스트 수정

`test_the_real_runner_reports_each_stage_while_it_runs`는 점검 결과가 없고 Spec이 AC 일부만 덮는 기록을 통과로 기대했다. 새 기준에서는 증거 부족이므로 픽스처에 증거를 채웠다. 단언은 그대로다.

## 검증

- `main` 기준선 132개, #24 기준선 143개, 이번 변경 후 185개 모두 통과
- 실행 환경: macOS, Python 3.13 venv, `python -m unittest discover -s tests`. `uv`가 없어 venv로 실행했다.
- 하지 않은 것: Docker 실행, 실제 모델 호출, 프런트엔드 빌드. 실제 실행 검증은 PR B 때 WSL2에서 한다.

## 남은 일

- PR B: Opus 진단, `check_diagnosis`, `run diagnose`, 프롬프트, 테스트 18~24·27
- #24 병합 후 PR A의 base를 `main`으로 변경
- #24의 "생성이 완료되었습니다" 문구: 수동 확인이 남아도 표시되는 문제에 대한 코멘트

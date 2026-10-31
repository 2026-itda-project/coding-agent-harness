# #10 PR B 변경 기록

Opus 원인 분석의 변경 내역이다. 비교 기준은 PR A(`feat/m2-opus-verification`, `7fd38c0`)이다. 계약 전문은 [e2e-contract.md 7절](e2e-contract.md#7-최종-판정-10)의 "Opus 진단"에 있다.

## 변경 파일

| 파일 | 변경 |
| --- | --- |
| `backend/app/m2/contracts.py` | `DiagnosisFailure`, `Diagnosis`, `check_diagnosis` |
| `backend/app/m2/stages.py` | `DIAGNOSIS_MAX_TOKENS`(16,000), `generate_diagnosis` |
| `backend/app/m2/acceptance.py` | `diagnosis_payload`, `evidence_hash`, `attach_diagnosis`. 판정 함수는 그대로 |
| `backend/app/m2/run.py` | `diagnose`, `cmd_diagnose`, `run diagnose`, `judge_run`의 진단 연결, 검증 뒤 자동 호출 |
| `backend/app/prompts/m2_verification_diagnosis.md` | 진단 프롬프트 (신규) |
| `backend/tests/test_m2_acceptance.py` | 진단 테스트 23개 추가 |
| `docs/m2/e2e-contract.md`, `docs/m2/review-runtime.md` | 진단 계약 |

## 달라지는 동작

- Run의 첫 검증이 `build_failed`, `auto_failed`이면 Opus를 1회 호출한다. **Run 비용이 늘어난다.** 예약은 입력 60,000 토큰, 출력 16,000 토큰 기준 약 USD 0.56이다.
- 재검증(`--resume`, 웹 `retry`)은 호출하지 않는다. #24의 "빌드와 점검만 다시 한다"는 안내를 지키기 위한 결정이다. 재검증 뒤 진단은 `run diagnose`로 요청한다.
- 진단이 거부·실패·예산 차단되어도 Run을 멈추지 않고, `final_status`와 `route`는 그대로다.
- `diagnosis.status`의 `not_implemented`가 사라졌다. 진단 전 기본값은 `not_run`이다.
- `handoff.suspected_task_ids`, `handoff.replan_scope`는 현재 증거로 만든 진단이 `done`일 때 채워진다.

## 검증

- 오프라인 테스트 208개 통과(PR A 185개 + 새 23개). 테스트의 모델 호출은 모두 가짜로 대체했다.
- 실제 Opus 호출 기록은 [run-20261031-diagnosis-01](runs/run-20261031-diagnosis-01.md)에 있다.
- **하지 않은 것:** Docker 실행, 실제 생성 앱에서의 진단.

## 실제 실행에서 확인할 것

- 실패가 많은 경우에도 `effort=high`에서 16,000 토큰이 충분한지. Spec은 생각 토큰만 26K를 쓴 기록이 있다. 잘리면 `incomplete_response`로 `failed`가 되고 판정은 유지된다.
- 실제 생성 앱의 로그로 만든 응답이 `check_diagnosis`를 통과하는지.
- 고의 실패 앱 1건과 성공 앱 1건의 결과를 `docs/m2/runs/`에 기록.

## 다른 PR과의 불일치 (미수정)

이 PR에서 고치지 않았고, 담당 범위에서 처리해야 한다.

- #24 화면: 수동 확인이 남아도 "생성이 완료되었습니다!"를 표시한다.
- #24 화면(#13): `manual_failed`에도 "빌드와 점검 다시 하기" 버튼이 나오지만 서버는 재시도를 실행하지 않는다. #26에서 `can_retry`를 제공하며, 화면이 이 값을 쓰도록 바꾸는 일이 남았다.
- #25 후속: 비용 보정 명령이 `run.json`을 고칠 때 `diagnosis.json`의 `call` 복사본은 옛 값으로 남는다.

## 한계

- 자동 재시도는 없다. 거부되거나 실패한 진단은 `run diagnose`로 다시 요청한다.
- 웹 API로는 진단을 요청할 수 없다. 범위에 CLI만 있었기 때문이다.
- `category`와 `root_cause`의 내용이 맞는지는 코드로 검증하지 않는다. 코드는 구조와 참조만 검사한다.

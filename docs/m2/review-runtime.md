# Review 결과에서 MVP 실행까지 (웹 연결)

기존 입력·Clarification·Review 화면과 M2 실행기(`backend/app/m2/run.py`)를 연결한 계약이다. 관련 이슈: #7, #8, #13(일부). 이 연결은 "앞뒤 연결"이며 M2 전체 완료(#9~#12의 Sandbox·Opus 검증·재계획·지표)를 뜻하지 않는다.

## 1. 변경 전과 후

```text
변경 전  입력 → (Mock/SnowChat) 요구사항·질문 → Review 승인 → 규칙 기반 PRD 미리보기 (끝)
         M2 실행은 사람이 고정 PRD 파일을 준비해 터미널에서 실행
변경 후  입력 → (LLM_PROVIDER=claude) Sonnet 요구사항·질문 → Review 승인
         → Sonnet M2 PRD (Review 결과 기준, 계약 검증) → 사용자 확인 → MVP 생성 요청
         → 기존 실행기 백그라운드 실행 (Opus Spec·Plan → Claude Code 코딩 → Docker 점검)
         → 화면에서 진행 상태, 실패 사유, 자동 점검 결과, 미리보기
```

기존 `/api/prd`(규칙 기반 M1 미리보기)와 Mock/SnowChat 경로는 그대로 둔다.

## 2. 요구사항 루프의 Claude 경로 (#7)

- `LLM_PROVIDER=claude`이면 `app/llm/claude.py`의 `ClaudeProvider`를 쓴다. 기존 Provider 인터페이스(`model_for`, `complete`)와 `pipeline.run_stage`의 출력 검증을 그대로 쓰고, 추출·Gap 분석·질문 생성·답변 반영 4단계 모두 `SONNET_MODEL`을 쓴다. 프롬프트는 기존 단계별 프롬프트를 공유한다.
- 응답 `analysis_mode`는 `claude`이며 화면에 "Claude Sonnet 분석 결과"로 표시한다. 단계 기록(`usage`)에 모델, 입력·출력 토큰, 계산 비용(`cost`)이 남는다.
- 오류는 명시적으로 실패한다: `missing_api_key`, `invalid_config`(모델 미설정), `authentication_failed`(401), `model_unavailable`(403·404·응답 모델 불일치), `rate_limit`, `empty_response`, `incomplete_response`, `structured_output_invalid`, `gateway_error`. Mock/SnowChat으로 대체하지 않는다. 검증에 실패한 유료 응답도 비용을 기록한다.
- 질문 라운드 정책은 기존과 같다(최대 3라운드, 이후 남은 Blocking은 AI Proposal로 전환). 2026-10-27 사용자 결정으로 Claude 경로도 M1 규칙을 유지한다. Review의 수정·개별/일괄 수락·Gap 정리·승인은 그대로이며 LLM을 호출하지 않는다. 입력은 기존 `text` 하나다(입력 양식 개편은 미정).

## 3. Review 결과 → M2 PRD (#8)

`POST /api/m2/prd` `{state}`: 승인된(`phase=approved`) `WorkflowState`를 받는다. 서버가 상태를 검증하고 `approve()`로 인계물(`ReviewedRequirements`)을 다시 만들어 Sonnet에 넘긴다. 같은 승인 상태는 같은 `prd_id`가 되어 다시 요청해도 모델을 다시 부르지 않는다.

Sonnet 입력: 원문(배경), 질문과 답변, 승인 인계물(Requirement의 id·내용·상태·출처·AC, `open_gaps`, `gap_links`). 프롬프트는 `prompts/m2_reviewed_prd_generation.md`이며 **Review 결과가 원문보다 우선**한다.

PRD 계약 확장(`app/m2/contracts.py`):
- `PRDItem`에 `requirement_ids`, `gap_ids`를 추가했다. 기본값은 빈 배열이라 기존 고정 PRD fixture와 CLI는 그대로 동작한다. 모델 출력용 `ReviewedPRD`에서는 필수다.
- `source`에 `clarification_answer`, `review_input`을 추가했다. 원문에서 바로 만든 PRD(`check_generated_prd`)에는 이 출처와 링크가 나올 수 없다.

Review 이후 검증 `check_reviewed_prd(prd, reviewed)`(기존 `check_prd` 포함):

| 구분 | 규칙 |
| --- | --- |
| 사용자 내용 (`initial_input`, `clarification_answer`, `review_input`) | 인계물의 같은 상태·출처 Requirement를 근거로 가져야 한다 |
| 수락된 AI 제안 (`confirmed` + `ai_proposal`) | 인계물에서 수락된(`confirmed` + `ai_proposal`) Requirement를 근거로 가질 때만 허용 |
| 미수락 AI 기본값 (`proposed` + `ai_proposal`) | 본문·구현 범위에 포함. 수락된 제안을 `proposed`로 되돌리면 거부 |
| 미결정 (`needs_clarification`) | 인계물의 미결정 Requirement는 모두 `open_issues`에 있어야 한다 |
| 누락 | 인계물의 모든 Requirement id가 어떤 항목에든 있어야 한다 |
| `open_gaps` | 모두 어떤 항목의 `gap_ids`에 있어야 한다. 관례 기본값이면 `proposed` + `ai_proposal` 항목, 서비스 성격이 달라지면 Open Issue (2026-10-27 사용자 결정) |
| `gap_links` | 연결된 Requirement를 담은 항목의 `gap_ids`에만 둔다. Open Issue로 다시 열면 거부한다. 단, 연결 대상이 미결정 Requirement면 그 Open Issue에 함께 남는다(M1 `build_prd`와 같은 규칙) |
| 참조 | 없는 Requirement·Gap id는 거부 |

검증에 실패하면 `prd.rejected-N.json`에 오류와 출력, 호출 기록을 남기고 실행할 수 없다. 호출이 실패하면 `prd.failed-N.json`에 남긴다. 규칙 기반 `build_prd`로 대체하지 않는다.

다시 생성할 때 예산 검사는 같은 `prd_id`의 이전 시도 기록까지 합산한다. 이전 시도에 비용 미확인 호출이 있으면 새 호출을 시작하지 않는다(409).

저장 위치: `HARNESS_WORKSPACE_ROOT/prds/<prd_id>/` — `prd.json`, `provenance.json`(분석 ID, 분석 모드, 요구사항 루프 usage, PRD 호출 기록과 비용, 프롬프트·PRD 해시), `reviewed.json`.

## 4. MVP 실행 (#13 일부)

| API | 동작 |
| --- | --- |
| `POST /api/m2/runs` `{prd_id, confirm_open_issues, retry}` | 서버에 저장된 그 PRD로 기존 실행기(`run.main`)를 백그라운드 스레드에서 시작하고 `run_id`를 바로 반환. `retry=true`는 실패·중단된 같은 Run을 실행기의 `--resume`으로 이어서 실행(사용자 요청 시에만) |
| `GET /api/m2/runs/{run_id}` | 단계, Task, 실패 사유, 자동 점검, 수동 확인 대기, 미리보기 주소, 비용 |
| `POST /api/m2/runs/{run_id}/preview/stop` | 그 Run의 미리보기 컨테이너만 종료 |

- 클라이언트는 `prd_id`만 보낸다. 파일 경로, PRD 내용, 명령, URL은 받지 않는다. id는 형식을 검사한 뒤에만 경로와 컨테이너 이름에 쓴다.
- `run_id = web-<prd_id>`라 같은 PRD의 중복 요청은 기존 Run을 돌려준다. 서버당 동시에 하나의 Run만 허용한다(다른 Run 실행 중이면 409). 실패 후 `retry` 없이 다시 요청해도 새로 실행하지 않는다. 다시 시도는 완료된 단계를 건너뛰고, 이전 시도의 오류(`web.json.previous_errors`)와 비용(`run.json`)을 그대로 남긴다. 자동 재시도는 없다.
- Opus Spec·Plan 호출의 출력 상한은 64,000토큰이다(`stages.OPUS_MAX_TOKENS`). 첫 실제 실행에서 항목이 많은 PRD의 Spec이 effort high의 생각 토큰(26K/32K)으로 32,000에서 잘렸다. 예산 예약도 같은 값으로 한다.
- Open Issue가 있으면 `confirm_open_issues=true`가 있어야 시작한다(2026-10-27 사용자 결정, 화면에서 체크).
- 예산: `M2_RUN_BUDGET_USD`(기본 8), `M2_TASK_BUDGET_USD`(기본 1.5), PRD 생성은 `M2_PRD_BUDGET_USD`(기본 0.5). 실행기의 기존 예산 검사와 비용 미확인 차단을 그대로 쓴다.
- 결과 표시(`outcome`):

  | outcome | 조건 |
  | --- | --- |
  | `running` | 실행 중 |
  | `failed` | 실행기가 멈춤(예산, 계약 거부, Task 실패 등). 사유 포함 |
  | `interrupted` | 실행 중에 서버가 재시작됨 |
  | `verification_failed` | 빌드 또는 자동 점검 실패 |
  | `auto_checks_passed` | 자동 점검(`automatic_status: passed`) 전부 통과. 실행기 `status`는 `passed` 또는 `manual_pending` |
  | `manual_check_only` | 자동 점검이 없고(`automatic_status: not_applicable`) 수동 확인만 남음. 자동 통과로 표시하지 않음 |

  마지막 두 경우도 완료가 아니다. `manual_review: pending`이면 화면에 "사람 확인 대기"로 표시한다. 미리보기 가능 여부와 검증 완료는 따로 표시한다.
- 추적: `web-<prd_id>/web.json`(PRD id, 시작·종료, 확인 여부, 예산) → `prds/<prd_id>/provenance.json`(분석 ID, 앞 단계 호출) → 기존 `run.json`·`artifacts/`.

## 5. 실행 방법

```bash
cd backend
docker build -t harness-sandbox:m2-1 app/m2/sandbox                         # 최초 1회
LLM_PROVIDER=claude uv run --env-file ../.env uvicorn app.main:app           # --reload·다중 worker 없이
cd ../frontend && npm run dev                                              # http://127.0.0.1:5173
```

`.env`: `ANTHROPIC_API_KEY`, `SONNET_MODEL`, `OPUS_MODEL`(기존 설정). Docker가 WSL에서 동작해야 MVP 실행이 가능하다.

## 6. 미리보기와 지원 환경

- 생성 앱은 Docker 컨테이너에서 `127.0.0.1:<임의 포트>`로 열리고, Harness 화면(5173)과 다른 origin에서 iframe과 새 창 링크로 보인다. 생성물은 기존 외부 작업 폴더와 로컬 Git(원격 없음) 구조 그대로다.
- **지원 환경: 브라우저와 실행기가 같은 컴퓨터**(WSL2의 경우 Windows 브라우저가 WSL의 127.0.0.1에 접근하는 기본 포워딩). 다른 컴퓨터의 브라우저에서는 미리보기 주소가 그 컴퓨터 자신을 가리키므로 동작하지 않는다.
- 미리보기는 서버를 꺼도 남는다. 화면의 "미리보기 종료" 또는 `docker rm -f harness-web-<prd_id>`로 끈다.

## 7. 한계 (임시 동작 포함)

- 실행 관리는 서버 프로세스 하나의 스레드다. `--reload`나 다중 worker에서는 쓰지 않는다. 서버가 재시작되면 진행 중이던 Run은 `interrupted`가 된다. 자동으로 다시 시작하지 않으며, 사용자가 "이어서 다시 시도"(`retry=true`)를 누르면 완료된 단계를 건너뛰고 이어서 실행한다. 재시작 중 이미 시작된 Claude Code 프로세스가 남을 수 있다.
- 자동 재시도·재계획(#11), Opus 최종 검증(#10), 비용·지표 체계(#12), 정식 Sandbox(#9)는 포함하지 않는다.
- 수동 확인 결과를 화면에서 기록하는 기능은 없다(`artifacts/manual-review.json`이 있으면 `done`).
- 화면 상태는 브라우저 메모리에만 있어 새로고침하면 진행 중인 PRD·Run을 다시 불러오지 못한다. 서버의 Run은 계속 진행되며 `GET /api/m2/runs/{run_id}`로 조회할 수 있다.
- Review 이후 PRD 프롬프트는 Structured Output 문법 한도 때문에 Spec과 같이 스키마를 프롬프트로 전달하고 같은 계약으로 검증한다.

## 8. 실제 실행 기록

- [run-20261027-web-01](runs/run-20261027-web-01.md): 웹 화면에서 입력부터 미리보기와 사람 확인까지 처음으로 끝까지 실행한 기록.

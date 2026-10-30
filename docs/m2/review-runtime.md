# Review 결과에서 MVP 실행까지 (웹 연결)

기존 입력·Clarification·Review 화면과 M2 실행기(`backend/app/m2/run.py`)를 연결한 계약이다. 관련 이슈: #7, #8, #13(일부). 이 연결은 "앞뒤 연결"이며 M2 전체 완료(#9~#12의 Sandbox·Opus 검증·재계획·지표)를 뜻하지 않는다.

## 1. 변경 전과 후

```text
변경 전  입력 → (Mock/SnowChat) 요구사항·질문 → Review 승인 → 규칙 기반 PRD 미리보기 (끝)
         M2 실행은 사람이 고정 PRD 파일을 준비해 터미널에서 실행
변경 후  입력 → (LLM_PROVIDER=claude) Sonnet 요구사항, 질문 → 검토
         → '생성' 버튼 한 번 (POST /api/m2/generate)
         → 백그라운드: Review 승인 → Sonnet M2 PRD(계약 검증) → 기존 실행기
           (Opus Spec, Plan → Claude Code 코딩 → Docker 빌드와 점검)
         → 화면에서 진행 단계, 실패 사유와 다음 행동, 완료 시 생성 앱 미리보기
```

기존 `/api/prd`(규칙 기반 M1 미리보기), `/api/m2/prd`(PRD만 생성)와 Mock/SnowChat 경로는 API로 남아 있지만 사용자 화면은 쓰지 않는다.

### 사용자 화면

입력 → 질문 → 검토 → 생성 → 완료의 단계형 화면이다. 사용자에게는 개발 산출물을 보여 주지 않는다.

- 검토 화면: 질문 답변과 Review에서 정한 내용, 중요한 AI 결정사항, 그 밖의 AI 기본값, 주 역할과 제외 범위를 보여 준다. 새 정책에서는 미정 사항을 검토 전에 보완한다. 기존 legacy 상태의 필수 미정 항목은 정해야 생성할 수 있다. 사용자가 처음 입력한 내용을 항목별로 다시 확인받지 않는다.
- 보이지 않는 것: 확정 요구사항 전체 목록, 남은 Gap과 연결 UI, AC 입력과 편집, 인계 JSON, PRD 원문과 내부 ID, Task 상세, 토큰과 비용. 내부 기록(`proposed`와 `ai_proposal` 출처, 비용과 비용 미확인 기록)은 그대로 저장한다. 내부 AC는 M2 PRD 생성 단계에서 LLM이 만들고 Spec 점검과 검증에 쓴다. 사용자가 보지 않았다는 이유로 `confirmed`로 바꾸지 않는다.
- 새 정책에서 문장을 고치면 `confirmed + review_input`, 추천을 그대로 수락하면 `confirmed + ai_proposal`이다. 이전 질문·추천·답변은 이력에 남는다. legacy 상태의 수정은 이전처럼 출처를 유지한다.
- 검토에서 정하거나 수정한 항목은 같은 화면에서 계속 확인·수정할 수 있다. 최초 입력의 확정 항목 전체를 선택적으로 펼쳐 보는 방식은 PR #24에서 논의 중이며 아직 구현하지 않았다(결정문 3절).
- 실행 상태를 처음 조회하거나 생성·재시도 중일 때는 홈 이동을 막아 저장된 실행 ID를 유지한다. 실패·확인 대기·완료 상태에서는 홈으로 돌아갈 수 있다. 실행 환경 오류는 앱의 빌드·점검 실패와 구분해 Docker 실행 상태와 샌드박스 설정을 확인하도록 안내한다.

## 2. 요구사항 루프의 Claude 경로 (#7)

- `LLM_PROVIDER=claude`이면 `app/llm/claude.py`의 `ClaudeProvider`를 쓴다. 기존 Provider 인터페이스(`model_for`, `complete`)와 `pipeline.run_stage`의 출력 검증을 그대로 쓰고, 모든 요구사항 단계는 `SONNET_MODEL`을 쓴다. 추출·Gap 분석 프롬프트는 공유하며, 새 정책의 질문 계획과 검토 보완은 `m2_clarification_plan.md`, `m2_review_completion.md`로 분리한다.
- 응답 `analysis_mode`는 `claude`다. 화면은 Claude가 아닌 분석(Mock, SnowChat)일 때만 검토 화면에 개발용 표시를 한다. 단계 기록(`usage`)에 모델, 입력·출력 토큰, 계산 비용(`cost`)이 남는다.
- 오류는 명시적으로 실패한다: `missing_api_key`, `invalid_config`(모델 미설정), `authentication_failed`(401), `model_unavailable`(403·404·응답 모델 불일치), `rate_limit`, `empty_response`, `incomplete_response`, `structured_output_invalid`, `gateway_error`. Mock/SnowChat으로 대체하지 않는다. 검증에 실패한 유료 응답도 비용을 기록한다.
- 새 화면은 분석 요청에 `policy=single_round_v1`을 명시한다. 최대 1라운드·5개 핵심 질문이며 0개도 가능하다. 기존 JSON/API 요청은 정책 생략 시 `legacy`로 읽으며 최대 3라운드, 기존 PRD 확인·재개 동작을 유지한다.
- 새 정책에서는 추출·Gap 분석 다음 `m2_clarification_plan`으로 질문을 선별한다. 모든 Gap을 상태에 보존하고, 답변 후(질문이 없으면 즉시) `m2_review_completion`으로 전체 요구사항, Gap별 결정 연결, 주 역할, 제외 범위를 만든다. 두 단계 모두 LLM 출력에 스키마·참조·원문 보존·출처 검증을 적용하며 잘못된 내용을 코드가 만들어 채우지 않는다.
- 질문은 `{gap_id, question, ai_proposal}`, 답변은 `{gap_id, answer, decision}`이다. `decision`은 `accept / reject / answer / skip`. 추천 수락은 서버가 해당 질문의 추천 문구를 사용한다. 거절은 대안 없이 제출할 수 없고 같은 화면에서 작성한다. 질문·추천·답변을 함께 Sonnet에 전달한다. 누락 답변과 빈 답변은 skip이며 다시 묻지 않는다.
- 수락은 `confirmed + ai_proposal`, 직접 작성·거절 대안은 `confirmed + clarification_answer`, 미응답·미질문 보완은 `proposed + ai_proposal`이다. 연결되지 않은 Gap과 non-blocking Gap도 포함한다. 질문 수를 줄이느라 제외한 핵심 결정은 Review의 중요한 AI 결정사항에서 보여 준다. 생성 버튼이 미수락 AI 제안을 확정하지 않는다.
- `selected_role_id`는 주 역할 Requirement 하나, `out_of_scope_ids`는 제외 역할·전용 기능의 Requirement ID들이다. 제외 항목은 삭제하지 않고 원문·상태·출처를 보존한다. 여러 역할을 입력하면 동일한 다섯 질문 안에서 선택한다. 역할 수정만 Sonnet을 다시 호출해 제외 범위를 정리하며, 일반 문장 수정·수락·승인은 모델을 호출하지 않는다.
- Gap 관리 API는 legacy 호환용으로 남으며 새 화면에서는 쓰지 않는다. 입력은 기존 `text` 하나다(입력 양식 개편은 미정).

### 라운드 제한 전환 기록

`WorkflowState`와 `ReviewedRequirements`의 `proposal_conversions`는 원본 `gap`, 전환 대상 `requirement_id`, `reason`, `round`를 보존한다. 새 정책의 reason은 `round_limit`(질문을 했으나 미응답), `question_limit`(질문 대상으로 선별하지 않은 핵심 Gap), `ai_default`(일반 보완)이며 round는 0 또는 1이다. legacy는 기존 round_limit·3을 유지한다. 기본값은 빈 배열이라 기존 JSON도 읽을 수 있다. 직접 답변과 명시적 추천 수락으로 만든 `gap_links`와 구분하며, 수락·수정 후에도 기록을 유지한다. LLM 추출 출력 스키마는 변경하지 않는다.

`approve()` metrics의 `forced_proposal_count`는 blocking Gap의 전환 건수(일반 보완과 명시적 추천 수락 제외), `unaccepted_forced_proposal_count`는 승인 시점에 아직 proposed인 전환 항목 수다. 기록은 `reviewed.json`에 저장되고 Run의 PRD 경로로 추적한다. 과거 전환 이력이 없는 JSON에 기록을 추측해 채우지 않는다. 기존 내용에 필드가 없거나 빈 경우 이전 PRD id를 유지하여 실패 시도 비용 차단을 우회하지 않는다.

새 정책에서는 중요한 AI 결정을 검토 화면에서 별도로 보여 주고 수정할 수 있다. 별도 확인창은 추가하지 않는다. 역할도 같은 검토 화면에서 확인·수정한다. legacy 실행의 생성 후 Open Issue 확인은 유지한다.


## 3. Review 결과 → M2 PRD (#8)

`POST /api/m2/prd` `{state}`: 승인된(`phase=approved`) `WorkflowState`를 받는다. 서버가 상태를 검증하고 `approve()`로 인계물(`ReviewedRequirements`)을 다시 만들어 Sonnet에 넘긴다. 같은 승인 상태는 같은 `prd_id`가 되어 다시 요청해도 모델을 다시 부르지 않는다.

Sonnet 입력: 원문(배경), 질문과 답변, 승인 인계물(Requirement의 id·내용·상태·출처·AC, `open_gaps`, `gap_links`). 프롬프트는 `prompts/m2_reviewed_prd_generation.md`이며 새 정책에는 `m2_single_role_policy.md`와 단일 역할 스택 계약을 덧붙인다. **Review 결과가 원문보다 우선**한다.

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
| `open_gaps` | 모두 어떤 항목의 `gap_ids`에 있어야 한다. 관례 기본값이면 `proposed` + `ai_proposal` 항목, 서비스 성격이 달라지면 Open Issue (팀 합의(PR #22 리뷰)에 따른 임시 결정) |
| `gap_links` | 연결된 Requirement를 담은 항목의 `gap_ids`에만 둔다. Open Issue로 다시 열면 거부한다. 단, 연결 대상이 미결정 Requirement면 그 Open Issue에 함께 남는다(M1 `build_prd`와 같은 규칙) |
| 참조 | 없는 Requirement·Gap id는 거부 |

검증에 실패하면 `prd.rejected-N.json`에 오류와 출력, 호출 기록을 남기고 실행할 수 없다. 호출이 실패하면 `prd.failed-N.json`에 남긴다. 규칙 기반 `build_prd`로 대체하지 않는다.

다시 생성할 때 예산 검사는 같은 `prd_id`의 이전 시도 기록까지 합산한다. 이전 시도에 비용 미확인 호출이 있으면 새 호출을 시작하지 않는다(409).

저장 위치: `HARNESS_WORKSPACE_ROOT/prds/<prd_id>/` — `prd.json`, `provenance.json`(분석 ID, 분석 모드, 요구사항 루프 usage, PRD 호출 기록과 비용, 프롬프트·PRD 해시), `reviewed.json`.

새 정책의 `PRD.policy`는 `single_round_v1`이며 주 역할 하나와 `open_issues=[]`를 검증한다. 선택 역할의 내용·상태·출처·참조를 보존하고, 제외 요구사항은 PRD `out_of_scope`에만 대응시킨다. 미수락 AI 기본값의 상태·출처도 바꾸지 않는다. 새 Open Issue가 나오면 재확인 화면으로 넘기지 않고 산출물 검증 실패로 처리한다. Spec 화면은 선택 역할만 참조하고 HTTP 점검의 `role_header`는 빈 값이다. Plan과 Task Context에도 단일 역할 스택 및 역할·제외 범위를 전달한다. 기존 fixture/CLI의 `policy=legacy`와 역할 전환 계약은 보존한다.

## 4. MVP 실행 (#13 일부)

| API | 동작 |
| --- | --- |
| `POST /api/m2/generate` `{state}` | 화면의 '생성' 버튼. `review` 또는 `approved` 상태를 받아 서버가 승인하고, 백그라운드 스레드에서 PRD 생성과 검증 후 실행기를 시작한다. `run_id`를 바로 반환한다. 같은 상태의 재요청은 같은 Run을 돌려준다 |
| `POST /api/m2/runs` `{prd_id, confirm_open_issues, retry}` | 서버에 저장된 그 PRD로 기존 실행기(`run.main`)를 백그라운드 스레드에서 시작하고 `run_id`를 바로 반환. `retry=true`는 실패·중단된 같은 Run을 실행기의 `--resume`으로 이어서 실행(사용자 요청 시에만) |
| `GET /api/m2/runs/{run_id}` | 현재 단계, Task, 비용 확인 필요 여부(`requires_cost_review`), 실패 사유(`error`는 내부 원문, `reason`은 화면용 짧은 문장), 자동 점검, 수동 확인 대기, 미리보기 주소, 비용, 확인이 필요한 미결정 사항 |
| `POST /api/m2/runs/{run_id}/preview/stop` | 그 Run의 미리보기 컨테이너만 종료 |

- 클라이언트는 `prd_id`만 보낸다. 파일 경로, PRD 내용, 명령, URL은 받지 않는다. id는 형식을 검사한 뒤에만 경로와 컨테이너 이름에 쓴다.
- `run_id = web-<prd_id>`라 같은 PRD의 중복 요청은 기존 Run을 돌려준다. 서버당 동시에 하나의 Run만 허용한다(다른 Run 실행 중이면 409). 실패 후 `retry` 없이 다시 요청해도 새로 실행하지 않는다. 다시 시도는 완료된 단계를 건너뛰고, 이전 시도의 오류(`web.json.previous_errors`)와 비용(`run.json`)을 그대로 남긴다. 자동 재시도는 없다.
- Opus Spec·Plan 호출의 출력 상한은 64,000토큰이다(`stages.OPUS_MAX_TOKENS`). 첫 실제 실행에서 항목이 많은 PRD의 Spec이 effort high의 생각 토큰(26K/32K)으로 32,000에서 잘렸다. 예산 예약도 같은 값으로 한다.
- 기존 legacy PRD에 Open Issue가 있으면 `confirm_open_issues=true`가 있어야 시작한다(팀 합의(PR #22 리뷰)에 따른 임시 결정). 생성 버튼 흐름에서는 PRD를 만든 뒤 `awaiting_confirmation`으로 멈추고, 화면이 미결정 사항을 보여 준 뒤 "이대로 계속 생성"으로 같은 Run을 이어 간다. 새 single_round_v1 PRD는 open_issues가 있으면 검증에서 거부하므로 이 확인 단계에 진입하지 않는다.
- 실패한 단계가 PRD 생성이면 `retry`는 PRD 단계부터 다시 하고, 실행기에서 멈췄으면 `--resume`으로 이어 간다. 빌드나 점검 실패(`verification_failed`)도 `retry`로 같은 코드를 다시 빌드하고 점검한다(모델 호출 없음). 코드를 고치거나 다시 계획하지 않으므로 코드 문제라면 같은 이유로 다시 실패할 수 있다.
- 예산: `M2_RUN_BUDGET_USD`(기본 8), `M2_TASK_BUDGET_USD`(기본 1.5), PRD 생성은 `M2_PRD_BUDGET_USD`(기본 0.5). 실행기의 기존 예산 검사와 비용 미확인 차단을 그대로 쓴다.
- 결과 표시(`outcome`):

  | outcome | 조건 |
  | --- | --- |
  | `running` | 실행 중 |
  | `awaiting_confirmation` | legacy PRD에 미결정 사항이 있어 사용자 확인을 기다림 |
  | `failed` | 실행기가 멈춤(예산, 계약 거부, Task 실패 등). 사유 포함 |
  | `interrupted` | 실행 중에 서버가 재시작됨 |
  | `verification_failed` | 빌드 또는 자동 점검 실패 |
  | `auto_checks_passed` | 자동 점검(`automatic_status: passed`) 전부 통과. 실행기 `status`는 `passed` 또는 `manual_pending` |
  | `manual_check_only` | 자동 점검이 없고(`automatic_status: not_applicable`) 수동 확인만 남음. 자동 통과로 표시하지 않음 |

  상단 단계 표시는 실행 결과 조회에 따라 생성에서 완료로 전환한다. 사용자 화면은 `auto_checks_passed`에만 "생성이 완료되었습니다!"를 쓴다. `manual_check_only`는 "생성이 끝났습니다"와 직접 확인 안내를 쓴다. 내부의 `manual_pending`과 `manual_review: pending`은 그대로 남기며 화면 문구 때문에 완료로 바꾸지 않는다. 실패, 중단, 점검 실패는 간단한 사유와 다음 행동(이어서 다시 시도, 홈으로)을 보여 준다.
- 진행 단계(`current_stage`)는 실행 기록에서만 계산하며, `done`이 아닌 단계(실패, 거부)는 다시 시도할 때 그 단계부터로 본다: `prd`(요구사항 정리) → `spec`(개발 명세) → `plan`(작업 계획) → `code`(기능 구현, 완료 Task 수 / 전체) → `build`(빌드) → `check`(최종 점검). 실행기는 시작할 때, Spec과 Plan을 마칠 때, Task 순서를 정할 때, Task마다, 빌드와 점검을 시작할 때(`verify_phase`) `run.json`을 저장한다. 화면은 10초마다 조회하고 예상 시간이나 가짜 진행률은 보여 주지 않는다.
- 서비스별 저장(대시보드는 아직 없음): `web-<prd_id>/web.json`(단계, 시작과 종료, 확인 여부, 예산, 이전 오류) → `prds/<prd_id>/state.json`(최초 입력, 질문과 답변, 검토 결과) → `reviewed.json`, `prd.json`, `provenance.json`(분석 ID, 앞 단계 호출과 비용) → 기존 `run.json`, `artifacts/`(Spec, Plan, Task 기록, 점검 결과)와 `app/`(생성 코드와 로컬 Git). 화면에서 홈으로 가도 이 파일들은 지우지 않는다.

## 5. 실행 방법

- Claude Code CLI가 설치되어 있고 실행하는 터미널에서 `claude --version`으로 확인되어야 한다(PATH 등록).
- Windows 네이티브 실행은 지원하지 않는다. WSL2 Ubuntu 터미널을 사용하고 Docker Desktop의 해당 배포판 통합을 켠다. `docker-desktop`만 설치되어 있다면 Ubuntu 배포판을 별도로 설치한다.
- `.env.example`을 복사했다면 `LLM_PROVIDER=snowchat`을 `LLM_PROVIDER=claude`로 바꾼다. 아래 명령은 같은 값을 명시적으로 지정한다.

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
- 미리보기는 서버를 꺼도 남는다. `POST /api/m2/runs/{run_id}/preview/stop` 또는 `docker rm -f harness-web-<prd_id>`로 끈다(사용자 화면에는 종료 버튼이 없다). 꺼진 미리보기를 화면에서 다시 켜는 기능은 없다.

## 7. 한계 (임시 동작 포함)

- 실행 관리는 서버 프로세스 하나의 스레드다. `--reload`나 다중 worker에서는 쓰지 않는다. 서버가 재시작되면 진행 중이던 Run은 `interrupted`가 된다. 자동으로 다시 시작하지 않으며, 사용자가 "이어서 다시 시도"(`retry=true`)를 누르면 완료된 단계를 건너뛰고 이어서 실행한다. 재시작 중 이미 시작된 Claude Code 프로세스가 남을 수 있다.
- 자동 재시도·재계획(#11), Opus 최종 검증(#10), 비용·지표 체계(#12)는 포함하지 않는다.
- 수동 확인 결과를 화면에서 기록하는 기능은 없다(`artifacts/manual-review.json`이 있으면 `done`).
- 새로고침: 생성을 시작하면 브라우저 `localStorage`에 `run_id`만 기억하고, 다시 열면 서버의 상태를 불러와 진행이나 결과 화면을 보여 준다. 진행 상태와 산출물은 서버 파일에 있으므로 브라우저 저장이 지워져도 생성 결과는 남는다(그 경우 화면에서 다시 찾아가는 목록은 아직 없다). 생성 전 단계(질문, 검토)의 화면 상태는 브라우저 메모리에만 있어 새로고침하면 처음부터 다시 해야 한다.
- Review 이후 PRD 프롬프트는 Structured Output 문법 한도 때문에 Spec과 같이 스키마를 프롬프트로 전달하고 같은 계약으로 검증한다.

### 비용 미확인 실패 복구 (운영자 수동 절차)

Claude 응답 중단과 Claude Code Agent Timeout은 비용이 `null`로 남을 수 있다. 비용이 미확인인 동안 다음 유료 호출은 차단한다. Run 화면은 재시도 버튼 대신 비용 확인 안내와 상태 새로고침을 제공한다. PRD 단계도 같은 실행 화면에서 비용 확인 필요를 알린다.

1. 해당 실행이 멈췄고 관련 Agent 프로세스도 종료됐는지 확인한다. 실행 중인 기록을 수정하지 않는다.
2. 아래 원본 JSON을 백업하고, 공급자 사용 내역과 호출 시각·모델·요청 식별자로 해당 호출 비용을 확인한다. 계정 전체 합계나 다른 호출의 비용을 대신 넣지 않는다. 호출별 비용을 확인할 수 없다면 잠금을 유지한다.
3. 확인된 비용만 해당 필드에 USD 숫자로 기록한다. 실패 기록·usage·attempt를 삭제하거나, 잠금을 풀기 위해 0 또는 임의 추정치를 넣지 않는다. 수정 일시·담당자·원래 값·확인된 금액·근거는 같은 폴더의 `cost-reconciliation.md`에 남긴다. 정식 보정·감사 기록 계약은 #12에서 정한다.

| 실패 단계 | 수정 대상 |
| --- | --- |
| PRD 생성 | `prds/<prd_id>/prd.failed-N.json` 또는 `prd.rejected-N.json`의 `calls[].cost_usd` |
| Spec / Plan | `<run_id>/run.json`의 `calls[].cost_usd` |
| Agent Timeout | `<run_id>/run.json`의 `attempts[].agent.total_cost_usd`와 같은 시도의 `tasks[task_id].agent.total_cost_usd`(존재할 때) |

4. 같은 PRD의 모든 실패 시도 또는 Run의 모든 호출·시도에 미확인 비용이 없는지 확인한다. 요약 비용만 수정해도 잠금은 풀리지 않는다. 예산 검사는 원본 호출·시도 기록을 다시 읽는다.
5. PRD는 같은 승인 상태로 다시 요청한다. Run은 화면에서 '비용 확인 상태 새로고침' 후 '이어서 다시 시도'를 누른다. 재개는 추가 비용이 발생하는 명시적 실행 요청이며, 자동 재개하지 않는다. 남은 예산이 부족하면 계속 중단된다. 자동 재개 정책은 #11에서 다룬다.

## 8. 실제 실행 기록

- [run-20261027-web-01](runs/run-20261027-web-01.md): 웹 화면에서 입력부터 미리보기와 사람 확인까지 처음으로 끝까지 실행한 기록.

- [check-20261030-user-flow](runs/check-20261030-user-flow.md): (가) 오프라인 124개와 Mock 브라우저 흐름 검증. 실제 모델 재실행은 하지 않았다.
- [run-20261030-user-flow](runs/run-20261030-user-flow.md): 후속 실제 모델 실행. Spec·코딩 실패 보완 후 같은 Run 재개, Task 5개와 Docker 자동 점검 11/11, 실제 브라우저 흐름·생성 앱 동작 확인. 실패 포함 약 USD 1.86.

## 9. 1라운드·단일 역할 정책 검증

`tests/test_m2_requirements.py`는 질문 0개·5개 상한, 연결되지 않은 Gap과 질문 제외 항목의 보존, 수락·거절·직접 답변의 출처, 잘못된 모델 출력 거부, 역할 수정, 예전 PRD ID, Spec·Plan·Context 정책 전달을 Mock으로 확인한다. `test_m2_web.py`는 새 정책 Review에서 PRD 생성 후 재확인 없이 실행기에 진입하고 기존 3라운드·실패 비용·재개가 유지되는지 검사한다.

브라우저 회귀 검사는 `frontend/tests/user_flow_smoke.py`와 `single_round_smoke.py`이며, 빌드된 화면의 요청을 로컬 fixture로 응답한다. 실제 Claude·Docker 생성 품질 검증을 뜻하지 않는다. 새 정책의 실제 모델 E2E는 별도 비용 승인 후 실행해야 한다. 질문 선별과 거절 대안의 의미 보존, 역할별 전용 기능 분리는 구조 검사만으로 품질을 보장할 수 없으므로 실제 결과를 확인해야 한다.

# M2 기본 E2E 계약 (E1~E6)

고정 PRD에서 생성 서비스 실행까지 한 경로를 연결하는 계약이다. 코드: `backend/app/m2/`. 정책 기준은 `docs/decisions/m2-development-direction.md`이며, 이 문서는 구현 계약이다.

## 1. 흐름과 모델

웹 화면에서 Review 결과로 PRD를 만들고 이 실행기를 시작하는 연결은 [review-runtime.md](review-runtime.md)에 있다. 아래 고정 PRD CLI 경로도 그대로 동작한다.

```text
예시 입력 --(Sonnet)--> 고정 PRD [E1]
고정 PRD --(Opus)--> Spec [E2] --(Opus)--> Feature·Task·Coding Plan [E3]
Task마다: Context 구성 [E4] -> Claude Code 헤드리스(Sonnet)가 작업 폴더 수정 -> Harness가 Task 커밋 [E5]
Docker 샌드박스에서 빌드·실행 -> Spec의 HTTP 점검 -> 재시작 후 지속성 점검 [E6]
```

- 모델 ID는 환경 변수 `SONNET_MODEL`, `OPUS_MODEL`에서만 읽는다. 다른 모델로 자동 대체하지 않는다.
- "Opus가 조종하는 Sonnet"은 Opus가 쓴 Task별 `instructions`를 Sonnet Agent가 실행하는 구조다.
- 자동 재시도와 재계획(#11)은 포함하지 않는다. 실행 증거로 내리는 최종 판정과 Opus 원인 분석은 [7절](#7-최종-판정-10)에 있다. 요구사항 루프와 UI 연결(#7, #8, #13 일부)은 이 문서가 아니라 [review-runtime.md](review-runtime.md)에서 다룬다.

## 2. PRD 입력 계약 (E1)

`backend/app/m2/contracts.py`의 `PRD`. 모든 필드가 필수이고 정의되지 않은 필드는 거부한다.

| 섹션 | ID 접두사 |
| --- | --- |
| in_scope / out_of_scope | `SCOPE-` / `OUT-` |
| roles | `ROLE-` |
| user_flows | `US-` |
| functional_requirements | `FR-` |
| business_rules | `RULE-` |
| nfrs | `NFR-` |
| open_issues | `ISSUE-` |
| acceptance_criteria | `AC-` (`target_ids`로 항목 참조) |

항목마다 `status`와 `source`를 둔다.

| source | status | 의미 |
| --- | --- | --- |
| `initial_input` | `confirmed` | 사용자가 입력에서 명시 |
| `ai_proposal` | `proposed` | 사용자가 말하지 않아 AI가 관례대로 정한 기본값. 본문과 구현에 포함하고 출처를 유지 |
| `ai_proposal` | `confirmed` | 사용자가 Review에서 수락한 AI 제안. 출처는 유지. 입력에서 바로 생성한 PRD에는 나올 수 없다 |
| `initial_input` 또는 `ai_proposal` | `needs_clarification` | `open_issues`에만. 서비스 성격이 달라지는 미결정 |
| `harness_policy` | `confirmed` | 생성 범위 제약(역할 전환, 모의 연동, 데이터 유지 등) |

검증 규칙 (`check_prd`):
- ID가 문서 전체에서 유일하고 섹션 접두사와 맞는다. 설명이 비어 있지 않다.
- `proposed`는 반드시 `ai_proposal`이다(역방향은 강제하지 않는다). `needs_clarification`은 `open_issues`에만 있다. `harness_policy`는 `confirmed`다.
- 입력에서 바로 생성한 PRD(`check_generated_prd`)에는 `confirmed + ai_proposal`이 있으면 안 된다. 사용자 수락이 없었기 때문이다.
- AC의 `target_ids`는 존재하는 항목이다. 모든 `FR-`에 AC가 하나 이상 있다.
- roles, user_flows, functional_requirements가 비어 있지 않다.

Assumptions는 별도 목록이 아니라 `status=proposed` 항목 전체로 계산한다.

Review 결과에서 만든 PRD(`ReviewedPRD`)는 같은 계약에 `requirement_ids`, `gap_ids`(필수)와 출처 `clarification_answer`, `review_input`을 더하고 `check_reviewed_prd`로 추가 검증한다. 규칙은 [review-runtime.md 3절](review-runtime.md#3-review-결과--m2-prd-8)에 있다.

## 3. 추적

PRD 항목 ID → Spec 항목의 `requirement_ids`, Spec 점검의 `ac_ids` → Plan Task의 `spec_ids`, `check_ids` → Task 커밋(`TASK-xxx: 제목 [done|failed]`) → 실행 기록 `artifacts/agent/TASK-xxx.json`, `artifacts/diffs/TASK-xxx.diff`.

- Spec 검증: 모든 참조가 PRD에 존재, 모든 AC가 점검으로 덮임, 모든 FR이 Spec에서 참조됨, API 경로는 `/api/`.
- Plan 검증: ID 중복, 존재하지 않는 Spec·점검·선행 Task, 순환 의존을 거부. 모든 점검이 어떤 Task에 배정됨.
- 코드는 실패한 산출물을 고쳐 통과시키지 않는다. 거부된 출력은 `<stage>.rejected.json`에 남긴다.

## 4. 생성 범위 (고정 스택 계약)

`backend/app/prompts/m2_stack_contract.md`: FastAPI + React(Vite) + SQLite, React 빌드를 FastAPI가 함께 제공, `data/app.db`에 초기 예시 데이터와 지속 저장, 실제 로그인 대신 역할 전환(`X-Role`), 외부 연동은 모의 동작으로 표시, 공유·내보내기·생성 후 변경 없음. 도메인 규칙은 Harness 코드에 넣지 않는다.

## 5. 작업 폴더와 실행 경계 (E5·E6)

- 위치: `HARNESS_WORKSPACE_ROOT`(기본 `~/dev/2026-itda-project/harness-workspaces`)`/<run_id>/`. `app/`이 생성 코드, `app.git/`이 그 로컬 Git 저장소(원격 없음), `artifacts/`가 실행 기록이다. Git 저장소를 작업 폴더 밖에 두므로 샌드박스와 Agent는 hook이나 Git 설정을 건드릴 수 없고, Harness가 호스트에서 실행하는 Git 명령이 생성 코드에 의해 조작되지 않는다. Harness 저장소 안 경로는 거부한다.
- Agent: `claude --bare -p`, `--permission-mode dontAsk`. `--tools Read,Edit,Write,Bash`로 도구를 요청하고, `Read(./**)`, `Edit(./**)`와 지정 샌드박스 스크립트만 허용한다. 파일 쓰기 권한은 Edit 경로 규칙으로 제한한다. `Write(path)` 규칙은 적용되지 않으므로 사용하지 않는다([공식 권한 문서](https://code.claude.com/docs/en/permissions#writepath-notebookeditpath-and-globpath-rules-never-match)). 도구를 요청했다고 CLI·모델이 항상 Write를 제공하는 것은 검증되지 않았다. 2026-10-30 실행에서는 Write 명시 후에도 에이전트가 Write 부재를 보고하고 호스트 파일 생성 명령을 시도해 거부됐다. 현재 CLI 2.1.312는 요청 목록에 Write가 있어도 모델에 Bash·Edit·Read만 제공했다. localhost 가짜 API를 이용한 무과금 검증에서 Edit의 `old_string=""`로 새 파일 생성이 가능함을 확인했다. 프롬프트에 이 방법을 명시하고, 필요한 디렉터리 생성과 빌드는 각각 짧은 샌드박스 호출로 수행한다. 폴더 밖 읽기는 거부됨을 확인했다(2026-10-15). Glob, Grep은 폴더 밖을 검색할 수 있어 허용하지 않으며, 검색은 샌드박스 안에서 한다. 생성물 Git에는 `app.git/info/exclude`로 `.git`, `node_modules/`, `data/`, `.venv/` 등을 항상 제외한다. `--bare`라서 인증은 `ANTHROPIC_API_KEY`만 사용한다(API 과금). Task별 `--max-budget-usd`.
- Agent는 호스트의 작업 폴더만 수정한다. 명령은 `backend/app/m2/sandbox/sandbox.sh`로만 실행하며, 작업 폴더만 마운트한 Docker 컨테이너(`harness-sandbox:m2-1`)에서 돈다. 패키지 설치를 위해 네트워크는 열려 있다.
- E6 빌드·실행도 같은 이미지에서 한다. 포트는 `127.0.0.1`에만 연다.

### 샌드박스 실행 제한과 정리 (#9)

실행 환경은 로컬 Docker다(결정문 5절). 확인한 환경은 로컬 WSL2(Ubuntu) + Docker Desktop이다. 검증 기록은 고정 점검용 앱으로 실패 경로를 확인한 [run-20261028-sandbox-01](runs/run-20261028-sandbox-01.md)과 실제 모델·생성 앱으로 확인한 [run-20261028-leave-01](runs/run-20261028-leave-01.md)이다.

- 제한(`agent.LIMITS`): 모든 샌드박스 컨테이너(`sandbox.sh`의 일회용 컨테이너와 E6 앱 서버)에 `--memory 3g --cpus 2 --pids-limit 512 --cap-drop ALL --security-opt no-new-privileges`를 건다. 컨테이너는 호출한 사용자의 UID로 돈다. `sandbox.sh`는 제한을 문자 그대로 적어 Agent가 환경변수로 풀 수 없게 했고, 테스트로 두 곳이 같은지 확인한다.
- 비밀정보: 컨테이너에 넘기는 환경변수는 `HOME`, `npm_config_cache`뿐이다. `ANTHROPIC_API_KEY` 등 호스트 환경변수는 넘기지 않는다.
- Timeout: `sandbox.sh` 명령마다 컨테이너 안에서 `timeout -k 10 ${SANDBOX_TIMEOUT:-900}`초(종료 코드 124, 종료 신호를 무시하면 10초 뒤 강제 종료되어 137). 호스트는 그보다 60초 길게 기다리고, 넘기면 프로세스 그룹을 종료한다. Agent는 `AGENT_TIMEOUT_S`(1800초).
- 프로세스 정리: Agent(`claude`)와 샌드박스 명령은 별도 프로세스 그룹으로 실행한다(`agent.run_group`). Timeout이나 Ctrl+C 때 그룹 전체를 종료한다. 웹 서버처럼 실행기를 스레드에서 돌려 Ctrl+C가 메인 스레드에만 전달되는 경우에도, Python이 종료될 때 남은 그룹을 종료하고 그 실행의 샌드박스 컨테이너를 지운다(`agent.stop_running`). 강제 종료(SIGKILL, 전원 차단)에는 적용되지 않는다.
- 컨테이너 정리: `sandbox.sh` 컨테이너에는 `harness.run=<run_id>` 라벨이 붙는다. docker CLI를 종료해도 컨테이너는 멈추지 않으므로, Task 시작 전과 끝난 뒤(성공·실패·Timeout·중단 모두), 그리고 E6 앞뒤로 이 라벨의 컨테이너를 강제 삭제한다. 삭제한 ID는 `agent/TASK-xxx-attempt-N.json`과 `verify.json`의 `leftover_containers`에 남긴다. Docker가 조회나 삭제에 실패하면(Timeout 포함) "남은 것 없음"으로 보지 않는다. Task 시작 전이면 Agent를 부르지 않고 실행을 멈추고, E6 시작 전이면 빌드 전에 멈춘다(`environment_error`). 끝난 뒤의 정리가 실패하면 Agent 결과·비용·커밋은 그대로 두고 `cleanup_error`에 사유를 남긴다. Task 뒤에는 그 시도를 저장한 다음 실행을 멈추고, E6 뒤에는 다음 Task나 검증이 시작 전 정리에서 다시 확인한다. 앱 서버(`harness-<run_id>`)에는 이 라벨이 없어 미리보기가 정리 대상이 되지 않는다.
- Context에는 작업 폴더 안의 실제 파일만 넣는다. 심볼릭 링크나 폴더 밖으로 이어지는 경로는 읽지 않고 `repository.skipped`에 이유를 남긴다.
- Task의 모든 시도를 `agent/TASK-xxx-attempt-N.json`, `diffs/TASK-xxx-attempt-N.diff`로 남기고, 예산은 모든 시도의 비용을 합산한다. 비용을 알 수 없는 기록이 있으면 다음 유료 호출을 시작하지 않는다.

## 6. Acceptance 판정 (E6)

- 자동: Spec `checks`의 `http_steps`(상태 코드, 응답 포함 문자열). 재시작 후 `rerun_after_restart` 점검을 다시 실행해 데이터 유지를 확인한다.
- 수동: 각 점검의 `manual_steps`를 브라우저에서 확인한다(`--keep-running`으로 미리보기 유지).
- 자동 판정 통과 조건: 최초 실행과 재시작 후 모두 `GET /`가 200이고, HTTP 단계가 있는 점검(최초 + 재시작 후 지속성 점검)이 모두 실행되어 통과. 실행되지 않은 자동 점검은 실패로 `failures`에 기록한다.
- 수동 전용 점검은 자동 점검 수에서 제외한다. 혼합 점검을 포함한 모든 수동 단계는 `manual_checks`에 `pending`으로 남긴다. 자동 판정은 `automatic_status`(passed/failed/not_applicable)로 구분하고, 실패 없이 수동 확인이 남으면 전체 `status`는 `manual_pending`이다. 수동 전용인 경우 자동 결과는 `not_applicable`이며 통과로 집계하지 않는다. 수동 판정은 7절의 기록 계약으로 입력한다.
- 완료 판정은 빌드 성공이 아니라 자동 점검 전부 통과와 수동 점검 기록이며, 7절의 `final_status`로 내린다. 시나리오별 체크리스트는 픽스처 폴더에 둔다.
- Test 단계: 스택 계약이 생성 앱에 테스트를 요구하지 않으므로 지금은 실행하지 않는다. 테스트 요구가 정해지면 스택 계약과 E6 단계에 추가한다.

### `verify.json` 기록 (#9)

| 필드 | 내용 |
| --- | --- |
| `environment` | Docker 서버 버전, 이미지 ID, 제한, `sandbox_timeout_s`. Docker나 이미지를 쓸 수 없으면 `error` |
| `steps[]` | `limits`, `build`, `start`, `restart` 순서의 실행 기록. 단계마다 `outcome`(`ok`/`failed`/`timeout`/`docker_error`/`killed`), `exit_code`(Timeout이면 124 또는 null. `killed`는 137로, 종료 신호를 무시해 강제 종료됐거나 메모리 초과로 종료된 경우이며 둘은 구분되지 않는다), `duration_s`, `output_tail` |
| `server_limits` | 앱 서버 컨테이너의 `docker inspect` 값(Memory, NanoCpus, PidsLimit, CapDrop, SecurityOpt). 빌드 컨테이너의 제한은 `limits` 단계 출력(cgroup의 memory.max, cpu.max, pids.max) |
| `root_status`, `root_wait` | 마지막 `GET /` 응답. HTTP 오류면 상태 코드(예: 500), 연결이 안 되면 `null`과 `error`(연결 오류 문구). 컨테이너가 멈췄으면 `container`(Running, ExitCode, OOMKilled, Error) |
| `root_status_after_restart`, `root_wait_after_restart` | 재시작 후 같은 기록 |
| `status` | `environment_error`(Docker·이미지·컨테이너 정리), `workspace_error`(생성 작업 폴더를 안전하게 다룰 수 없음), `build_failed`, `start_failed`, `checks_failed`, `manual_pending`, `passed` |
| `failure_stage` | 실패한 단계: `preflight`(Docker·이미지·시작 전 정리), `data`(검증 전 데이터 보관), `build`, `start`(컨테이너 실행), `serve`(앱이 200으로 응답하지 않음), `restart`(재시작 실패 또는 재시작 후 응답 없음), `checks`(점검 실패). 성공이면 null |
| `failures[]` | 사람이 읽는 실패 사유 |
| `passed`, `total`, `automatic_status`, `manual_checks` | 통과한 자동 점검 수, 계획된 자동 점검 수, 자동 판정, 수동 확인 대기 목록. 점검 전에 멈춘 검증(`environment_error`·`build_failed`·`start_failed`)도 실행되지 않은 점검을 실패로 세어 `0/total`, `failed`로 남기고 `failures`에 "N planned checks did not run"을 더한다 |
| `server_log_tail` | 앱 서버 로그 마지막 80줄. stdout(접근 로그)과 stderr(기동 오류, Traceback)를 함께 남긴다 |
| `leftover_containers` | E6 앞뒤로 정리한 이 실행의 `sandbox.sh` 컨테이너 ID |
| `cleanup_error` | E6 뒤 컨테이너 정리가 실패한 사유. 정리를 확인하지 못했다는 뜻이며 판정(`status`)은 바꾸지 않는다. Task 기록(`agent/TASK-xxx-attempt-N.json`)에도 같은 필드가 있다 |
| `data_kept` | 검증 전에 작업 폴더의 `data/`(런타임 DB)를 옮겨 둔 위치(`<run_id>/data-before-verify/`, 마지막 1개만 보관). 없었으면 null. Agent가 코딩 중 직접 보낸 요청이나 이전 검증의 점검 데이터가 남아 결과를 바꾸지 않도록, 검증은 항상 생성 코드가 만드는 시드 데이터에서 시작한다. 그래서 같은 Run을 다시 검증하면 미리보기에서 입력한 데이터도 시드 상태로 돌아가고, 그 직전 데이터 1개만 보관본으로 남는다. 옮긴 데이터는 샌드박스에 마운트되지 않고 Git에도 들어가지 않는다. `data/`나 보관 경로가 심볼릭 링크면 따라가지 않고 `workspace_error`(`data`)로 멈추며, 이전 보관본을 지우지 못해도 멈춘다 |

이 기록은 실패를 구분할 원자료다. 어떤 실패를 자동 수정·재시도할지는 #11에서 정한다.

## 7. 최종 판정 (#10)

코드(`backend/app/m2/acceptance.py`)가 실행 증거로만 판정한다. 입력은 `verify.json`, PRD·Spec·Plan, `manual-review.json`, Task 기록이다. 모델 출력은 판정 입력이 아니다. 판정에는 유료 호출이 없고, 결과는 `artifacts/verification.json`과 `run.json`의 `final_status`에 남는다. 판정을 저장한 뒤 필요한 경우에만 Opus가 원인을 분석한다.

### `final_status` (위에서부터 처음 해당하는 값)

| 값 | 조건 | `handoff.route` |
| --- | --- | --- |
| `insufficient_evidence` | `verify.json` 없음·손상, `environment_error`, `workspace_error`, `start_failed`, 어떤 단계든 `docker_error`, 계획과 맞지 않는 기록(모르는·중복·누락 결과, 다른 Spec, 점검 없는 AC, 끝나지 않은 Task, build 기록 없음) | `environment` |
| `build_failed` | 빌드 단계 실패. `timeout`·`killed`는 원인을 구분할 수 없어 `cause_ambiguous: true` | `replan_candidate` |
| `test_failed` | `verify.json`의 `test.status`가 `failed`. **현재 스택 계약에 Test 단계가 없어 실제 실행 경로가 없다** | `replan_candidate` |
| `auto_failed` | 점검 실패, `GET /`가 200이 아님, 재시작 실패 | `replan_candidate` |
| `manual_failed` | 현재 코드에 대한 수동 `fail`이 있음 | `replan_candidate` |
| `manual_pending` | 미기록이거나 다른 코드의 기록만 있는 수동 단계가 있음 | `wait_for_human` |
| `passed` | 계획된 자동 점검이 모두 실행·통과했고 수동 단계도 모두 `pass` | `none` |

`automatic_status`나 `status`를 믿지 않고 점검 결과에서 다시 계산한다. `stopped()`가 실행하지 못한 점검도 `failed`로 적기 때문이다. Test 게이트는 `not_applicable`로 기록하고 통과로 세지 않는다.

### `verification.json`

| 필드 | 내용 |
| --- | --- |
| `final_status`, `reasons`, `cause_ambiguous` | 판정, 사유, 원인 구분 불가 여부 |
| `check_authorship` | `spec_model`. 자동 점검은 Spec을 작성한 모델이 만들었으므로 독립 검증이 아니다 |
| `basis` | `code_commit`, `spec_sha256`, `verify_status`, `failure_stage`, `verify_failures`, `manual_review_state`(`none`·`recorded`·`invalid`) |
| `gates` | `build`, `test`, `automatic`, `manual`별 상태와 수 |
| `checks[]` | 점검별 `first_run`, `after_restart`, `manual[]`, `state`, `ac_ids`, `task_ids` |
| `acceptance[]` | AC별 `state`, `check_ids`, `task_ids` |
| `tasks[]` | Task별 `agent_status`, `attempt`, `commit`, `check_ids`, `state`(점검이 없으면 `no_checks`), `diff` |
| `evidence_refs` | `verify.json`, 서버 로그, 수동 기록 위치 |
| `diagnosis` | Opus 원인 분석. 아래 "Opus 진단" 참고 |
| `handoff` | 재계획 인계. 아래 참고 |

점검·AC·Task 상태는 가장 나쁜 값을 따른다: `no_evidence` > `auto_failed` > `manual_failed` > `not_run` > `manual_pending` > `manual_passed` > `auto_passed`.

### 수동 확인 기록

제출 형식은 `{reviewer, items: [{check_id, step_index, step, verdict: pass|fail, evidence}]}`이다. 경로는 CLI `run manual-review`와 `POST /api/m2/runs/{run_id}/manual-review` 두 가지다. 시각과 코드 커밋은 서버가 기록한다.

- 거부: 빈 `reviewer`·`evidence`, 모르는 점검, 계획에 없는 단계, Spec과 다른 문구, 중복, 빈 `items`, 점검 전에 멈춘 검증, 어느 코드를 점검했는지 모르는 `verify.json`. 입력과 오류는 `manual-review.rejected.json`에 남고 기존 기록은 바뀌지 않는다.
- 부분 제출을 허용한다. `manual-review.json`에 단계별로 합치고, 받아들인 제출과 그로 대체된 판정은 `manual-review.history.jsonl`에 쌓는다.
- 항목마다 `code_commit`, `spec_sha256`을 남긴다. 같은 커밋을 다시 검증하면 수동 판정을 유지하고, 코드나 Spec이 바뀌면 `stale`로 세어 다시 확인을 요구한다.
- 판정 때 파일을 다시 검증한다. 계약에 맞지 않으면 전체를 쓰지 않으며(`invalid`), 파일이 있다는 것만으로 완료되지 않는다. 그런 파일이 있으면 새 제출도 거부하므로 운영자가 옮겨야 한다.
- 한계: 재검증은 데이터를 시드로 되돌리므로(`data_kept`), 유지된 수동 판정은 그 당시 화면을 확인한 결과다. `reviewer`는 본인이 입력한 값이며 인증하지 않는다(로컬 데모 범위).

### 다시 판정하기

- `run judge --run-dir <dir>`: Docker와 모델을 부르지 않고 판정만 다시 한다. 수동 기록을 넣으면 같은 판정이 자동으로 실행된다. 실행 중인 웹 Run에는 쓰지 않는다.
- `run --resume`: 검증을 다시 실행한다. 시작할 때 `verification.json`을 지우고 `verify.json`을 `verify.previous.json`으로 옮겨, 중단되어도 이전 판정이 남지 않는다. 중단된 Run은 `insufficient_evidence`다.
- `verify.json`에 `code_commit`, `spec_sha256`이 추가되었다. 이전 기록에는 없으며, 그 기록에 대한 수동 제출은 거부된다.

### Opus 진단

판정을 설명할 뿐 바꾸지 못한다. 출력 계약(`contracts.Diagnosis`)에 판정 필드가 없고 정의되지 않은 필드는 거부한다.

- 입력(`acceptance.diagnosis_payload`): 실패 목록, 미통과 AC, 단계 결과, 실패 단계의 출력 꼬리, 서버 로그 꼬리, 실패 점검 응답, 관련 Task의 지시·파일·`diff_stat`. 로그와 응답 본문은 생성 코드에서 온 데이터로 다룬다.
- 출력: `summary`, `failures[]`(`stages`, `check_ids`, `ac_ids`, `category`, `root_cause`, `evidence`, `suspected_task_ids`, `fix_hint`), `replan_scope`(`task`·`plan`·`spec`·`environment`).
- 검증(`check_diagnosis`): 참조는 입력에 있어야 한다. 실패 단계와 실패 점검을 모두 덮어야 하고, 실패하지 않은 단계는 넣을 수 없으며, 근거가 하나 이상 있어야 한다. 근거는 모델이 내용을 받은 항목(`evidence_index`)만 쓴다. 전체 diff는 입력에 없으므로 근거가 될 수 없다. 환경 실패면 `replan_scope`는 `environment`여야 한다. 코드가 앱 실패로 본 증거(`cause_ambiguous`도 아닌 경우)에는 `environment`를 쓸 수 없다. 거부된 출력은 `diagnosis.rejected.json`에 남기고 고쳐 쓰지 않는다.
- 호출: 이 Run의 첫 검증(`verify.json`, `verify.previous.json`이 없을 때)이 `build_failed`, `auto_failed`이면 자동으로 1회 호출한다. `--resume`이나 웹 `retry`의 재검증은 호출하지 않는다. 재검증 뒤 진단, `manual_failed`와 `test_failed`, 실패한 진단의 재시도는 명시적 `run diagnose`로만 호출한다. 수동 기록 제출과 `run judge`, 상태 조회는 호출하지 않는다.
- 예산: 시스템 프롬프트, 스키마, 입력을 합친 요청의 UTF-8 바이트 수를 입력 토큰 수로 보고, 출력은 `stages.DIAGNOSIS_MAX_TOKENS`(16,000)로 `Budget.reserve`를 한다. 요청이 100,000바이트를 넘으면 호출하지 않는다. 비용 미확인 기록이 있으면 호출하지 않는다. 출력 비용은 `max_tokens`로 제한되지만 입력 토큰 수는 호출 전 추정치다. 따라서 예약액은 실제 비용의 상한이 아니며, 제공자 측 청구 한도도 아니다.
- 기록: `artifacts/diagnosis.json`. `call`에 응답의 실제 모델 ID, usage, 비용이 있고 `run.json`의 `calls[]`에도 `stage: diagnosis`로 남는다.
- 재호출 방지: `evidence_sha256`은 입력 해시다. 소요 시간은 입력에 없으므로 같은 코드를 재검증해 결과와 로그가 같으면 진단이 `done`으로 유지된다. 로그 내용이 달라지면 `stale`이 되고, `run diagnose`를 실행해야 다시 호출한다. 실제 앱의 서버 로그와 응답에는 임시 포트와 시각이 들어가므로, 재검증하면 보통 `stale`이 된다([실행 기록](runs/run-20261031-diagnosis-02.md)).

| `diagnosis.status` | 뜻 |
| --- | --- |
| `not_needed` | `passed`, `manual_pending`이라 분석할 실패가 없음 |
| `skipped_environment` | `insufficient_evidence`. 자동 호출하지 않음. 실패 증거가 함께 있으면 `run diagnose` 가능 |
| `not_run` | 진단 대상이지만 아직 호출하지 않음. 첫 검증이 환경 오류였고 재검증에서 앱 실패가 나온 경우 등 |
| `stale` | 현재 증거로 만든 진단이 없음. 진단 뒤 증거가 바뀌었거나 수동 실패가 기록됨 |
| `done` | 현재 증거로 만든 진단이 계약을 통과함 |
| `rejected`, `failed`, `budget_stopped` | 계약 거부, 모델 호출 실패, 예산·비용 미확인 차단. `error`에 사유가 있으며 판정은 그대로다 |

### `handoff` (#11 인계)

`final_status`, `route`, `cause_ambiguous`, `reasons`, `failures[]`, `suspected_task_ids`, `replan_scope`. `failures[]`는 코드가 만들며 항목은 `kind`(`build`·`start`·`restart`·`serve`·`serve_after_restart`·`auto_check`·`manual_step`), `check_id`, `ac_ids`, `task_ids`, `detail`, `evidence_ref`다. `task_ids`는 Plan의 `check_ids` 배정이며 원인 Task라는 뜻이 아니다. `suspected_task_ids`와 `replan_scope`는 현재 증거로 만든 진단이 `done`일 때만 값이 있고, 그 외에는 빈 배열과 `null`이다. `replan_scope`는 Opus의 제안이며 `route`는 바꾸지 못한다.

### 미결정

자동 전용 점검의 `passed`를 최종 완료로 인정하되 `check_authorship`으로 표시한다. 모든 AC에 수동 단계를 요구하는 등의 더 엄격한 기준은 정하지 않았다.

## 8. 실행

실제 실행을 확인한 환경은 WSL2(Ubuntu) + Docker Desktop입니다. Windows에서는 WSL 터미널에서 실행합니다. 현재 실행기는 Bash와 POSIX API를 사용하므로 Windows 네이티브 실행은 지원하지 않습니다. macOS 및 별도 Linux 환경에서는 아직 실제 E2E를 검증하지 않았습니다.

```bash
cd backend
docker build -t harness-sandbox:m2-1 app/m2/sandbox      # 최초 1회
uv run --env-file ../.env python -m app.m2.run prd --input ../docs/m2/fixtures/leave-management/input.md --out ../docs/m2/fixtures/leave-management
uv run --env-file ../.env python -m app.m2.run run --prd ../docs/m2/fixtures/leave-management/prd.json --keep-running
uv run --env-file ../.env python -m app.m2.run run --resume <run dir>   # 실패 지점부터 이어 실행
uv run python -m app.m2.run manual-review --run-dir <run dir> --input <review.json>   # 수동 확인 기록
uv run python -m app.m2.run judge --run-dir <run dir>                    # 다시 판정 (Docker·모델 호출 없음)
uv run --env-file ../.env python -m app.m2.run diagnose --run-dir <run dir>   # 원인 분석 (유료, Opus 1회)
uv run python -m unittest discover -s tests                              # 오프라인 테스트
```

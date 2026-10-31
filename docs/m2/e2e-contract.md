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
- 자동 재시도와 Opus 검증, 재계획(#10, #11)은 포함하지 않는다. 요구사항 루프와 UI 연결(#7, #8, #13 일부)은 이 문서가 아니라 [review-runtime.md](review-runtime.md)에서 다룬다.

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
- Agent: `claude --bare -p`, `--permission-mode dontAsk`, 허용 도구는 작업 폴더 안으로 제한한 Read, Edit, Write(`./**`)와 샌드박스 스크립트 실행뿐이다. Claude Code 2.1.312의 `--bare`에는 Write 도구가 없어(제공 도구: Bash, Edit, Read) 새 파일도 Edit로 만든다(2026-10-28 확인). 폴더 밖 읽기는 거부됨을 확인했다(2026-10-15). Glob, Grep은 폴더 밖을 검색할 수 있어 허용하지 않으며, 검색은 샌드박스 안에서 한다. 생성물 Git에는 `app.git/info/exclude`로 `.git`, `node_modules/`, `data/`, `.venv/` 등을 항상 제외한다. `--bare`라서 인증은 `ANTHROPIC_API_KEY`만 사용한다(API 과금). Task별 `--max-budget-usd`.
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

### 비용·시간 기록 (#12)

모델 호출 기록(`run.json`의 `calls[]`, PRD 폴더의 `provenance.json`·`prd.failed-N.json`·`prd.rejected-N.json`의 `calls[]`)과 Task 시도 기록(`attempts[]`, `agent/TASK-xxx-attempt-N.json`)은 비용을 어떻게 얻었는지와 시각을 함께 남긴다. 비용을 알 수 없으면 `cost_usd`(시도는 `agent.total_cost_usd`)는 `null`이고 0으로 바꾸지 않는다.

| 필드 | 값 |
| --- | --- |
| `cost_status` | `computed`: usage × 요율로 계산(호출) · `reported`: Claude Code가 보고한 합계(시도, 내부 호출 포함) · `unknown`: 알 수 없음 |
| `unknown_reason` | `unknown`일 때만 값이 있다. 호출: `stream_interrupted`(응답 도중 끊김), `usage_missing`(토큰 수 없음), `cost_calculation_failed`(요율 없는 모델·캐시 과금). 시도: `agent_timeout`, `cost_not_reported` |
| `pricing` | `computed`일 때 사용한 요율(`input_usd_per_mtok`, `output_usd_per_mtok`)과 출처(`source`, `url`, `checked`), 계산식(`basis`). 값은 `app/llm/claude.py`의 `PRICES`·`PRICING` |
| `started_at`, `ended_at` | ISO 8601 UTC. 호출은 `latency_s`, 시도는 `duration_s`(초)도 남긴다 |

이 필드가 없는 이전 기록은 기존처럼 `cost_usd`의 `null` 여부로만 판단한다. 미확인 비용의 확인·보정 절차는 후속 PR에서 정한다(현재는 `review-runtime.md` 7절의 수동 절차).

## 6. Acceptance 판정 (E6)

- 자동: Spec `checks`의 `http_steps`(상태 코드, 응답 포함 문자열). 재시작 후 `rerun_after_restart` 점검을 다시 실행해 데이터 유지를 확인한다.
- 수동: 각 점검의 `manual_steps`를 브라우저에서 확인한다(`--keep-running`으로 미리보기 유지).
- 자동 판정 통과 조건: 최초 실행과 재시작 후 모두 `GET /`가 200이고, HTTP 단계가 있는 점검(최초 + 재시작 후 지속성 점검)이 모두 실행되어 통과. 실행되지 않은 자동 점검은 실패로 `failures`에 기록한다.
- 수동 전용 점검은 자동 점검 수에서 제외한다. 혼합 점검을 포함한 모든 수동 단계는 `manual_checks`에 `pending`으로 남긴다. 자동 판정은 `automatic_status`(passed/failed/not_applicable)로 구분하고, 실패 없이 수동 확인이 남으면 전체 `status`는 `manual_pending`이다. 수동 전용인 경우 자동 결과는 `not_applicable`이며 통과로 집계하지 않는다. 수동 판정 입력 기능은 후속 범위다.
- 완료 판정은 빌드 성공이 아니라 자동 점검 전부 통과와 수동 점검 기록이다. 시나리오별 체크리스트는 픽스처 폴더에 둔다.
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

## 7. 실행

실제 실행을 확인한 환경은 WSL2(Ubuntu) + Docker Desktop입니다. Windows에서는 WSL 터미널에서 실행합니다. 현재 실행기는 Bash와 POSIX API를 사용하므로 Windows 네이티브 실행은 지원하지 않습니다. macOS 및 별도 Linux 환경에서는 아직 실제 E2E를 검증하지 않았습니다.

```bash
cd backend
docker build -t harness-sandbox:m2-1 app/m2/sandbox      # 최초 1회
uv run --env-file ../.env python -m app.m2.run prd --input ../docs/m2/fixtures/leave-management/input.md --out ../docs/m2/fixtures/leave-management
uv run --env-file ../.env python -m app.m2.run run --prd ../docs/m2/fixtures/leave-management/prd.json --keep-running
uv run --env-file ../.env python -m app.m2.run run --resume <run dir>   # 실패 지점부터 이어 실행
uv run python -m unittest tests.test_m2                                  # 오프라인 테스트
```

# M2 기본 E2E 계약 (E1~E6)

고정 PRD에서 생성 서비스 실행까지 한 경로를 연결하는 계약이다. 코드: `backend/app/m2/`. 정책 기준은 `docs/decisions/m2-development-direction.md`이며, 이 문서는 구현 계약이다.

## 1. 흐름과 모델

```text
예시 입력 --(Sonnet)--> 고정 PRD [E1]
고정 PRD --(Opus)--> Spec [E2] --(Opus)--> Feature·Task·Coding Plan [E3]
Task마다: Context 구성 [E4] -> Claude Code 헤드리스(Sonnet)가 작업 폴더 수정 -> Harness가 Task 커밋 [E5]
Docker 샌드박스에서 빌드·실행 -> Spec의 HTTP 점검 -> 재시작 후 지속성 점검 [E6]
```

- 모델 ID는 환경 변수 `SONNET_MODEL`, `OPUS_MODEL`에서만 읽는다. 다른 모델로 자동 대체하지 않는다.
- "Opus가 조종하는 Sonnet"은 Opus가 쓴 Task별 `instructions`를 Sonnet Agent가 실행하는 구조다.
- 자동 재시도, Opus 검증·재계획(#10·#11), UI 연결(#13), 요구사항 루프 연결(#7·#8)은 포함하지 않는다.

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

## 3. 추적

PRD 항목 ID → Spec 항목의 `requirement_ids`, Spec 점검의 `ac_ids` → Plan Task의 `spec_ids`, `check_ids` → Task 커밋(`TASK-xxx: 제목 [done|failed]`) → 실행 기록 `artifacts/agent/TASK-xxx.json`, `artifacts/diffs/TASK-xxx.diff`.

- Spec 검증: 모든 참조가 PRD에 존재, 모든 AC가 점검으로 덮임, 모든 FR이 Spec에서 참조됨, API 경로는 `/api/`.
- Plan 검증: ID 중복, 존재하지 않는 Spec·점검·선행 Task, 순환 의존을 거부. 모든 점검이 어떤 Task에 배정됨.
- 코드는 실패한 산출물을 고쳐 통과시키지 않는다. 거부된 출력은 `<stage>.rejected.json`에 남긴다.

## 4. 생성 범위 (고정 스택 계약)

`backend/app/prompts/m2_stack_contract.md`: FastAPI + React(Vite) + SQLite, React 빌드를 FastAPI가 함께 제공, `data/app.db`에 초기 예시 데이터와 지속 저장, 실제 로그인 대신 역할 전환(`X-Role`), 외부 연동은 모의 동작으로 표시, 공유·내보내기·생성 후 변경 없음. 도메인 규칙은 Harness 코드에 넣지 않는다.

## 5. 작업 폴더와 실행 경계 (E5·E6)

- 위치: `HARNESS_WORKSPACE_ROOT`(기본 `~/dev/2026-itda-project/harness-workspaces`)`/<run_id>/`. `app/`이 생성 코드, `app.git/`이 그 로컬 Git 저장소(원격 없음), `artifacts/`가 실행 기록이다. Git 저장소를 작업 폴더 밖에 두므로 샌드박스와 Agent는 hook이나 Git 설정을 건드릴 수 없고, Harness가 호스트에서 실행하는 Git 명령이 생성 코드에 의해 조작되지 않는다. Harness 저장소 안 경로는 거부한다.
- Agent: `claude --bare -p`, `--permission-mode dontAsk`, 허용 도구는 작업 폴더 안으로 제한한 Read, Edit, Write(`./**`)와 샌드박스 스크립트 실행뿐이다. 폴더 밖 읽기는 거부됨을 확인했다(2026-10-15). Glob, Grep은 폴더 밖을 검색할 수 있어 허용하지 않으며, 검색은 샌드박스 안에서 한다. 생성물 Git에는 `app.git/info/exclude`로 `.git`, `node_modules/`, `data/`, `.venv/` 등을 항상 제외한다. `--bare`라서 인증은 `ANTHROPIC_API_KEY`만 사용한다(API 과금). Task별 `--max-budget-usd`.
- Agent는 호스트의 작업 폴더만 수정한다. 명령은 `backend/app/m2/sandbox/sandbox.sh`로만 실행하며, 작업 폴더만 마운트한 Docker 컨테이너(`harness-sandbox:m2-1`)에서 돈다. 패키지 설치를 위해 네트워크는 열려 있다.
- E6 빌드·실행도 같은 이미지에서 한다. 포트는 `127.0.0.1`에만 연다.
- Context에는 작업 폴더 안의 실제 파일만 넣는다. 심볼릭 링크나 폴더 밖으로 이어지는 경로는 읽지 않고 `repository.skipped`에 이유를 남긴다.
- Task의 모든 시도를 `agent/TASK-xxx-attempt-N.json`, `diffs/TASK-xxx-attempt-N.diff`로 남기고, 예산은 모든 시도의 비용을 합산한다. 비용을 알 수 없는 기록이 있으면 다음 유료 호출을 시작하지 않는다.

## 6. Acceptance 판정 (E6)

- 자동: Spec `checks`의 `http_steps`(상태 코드, 응답 포함 문자열). 재시작 후 `rerun_after_restart` 점검을 다시 실행해 데이터 유지를 확인한다.
- 수동: 각 점검의 `manual_steps`를 브라우저에서 확인한다(`--keep-running`으로 미리보기 유지).
- 자동 판정 통과 조건: 최초 실행과 재시작 후 모두 `GET /`가 200이고, 계획된 점검(최초 전체 + 재시작 후 지속성 점검)이 모두 실행되어 통과. 실행되지 않은 점검은 실패로 `failures`에 기록한다.
- 완료 판정은 빌드 성공이 아니라 자동 점검 전부 통과와 수동 점검 기록이다. 시나리오별 체크리스트는 픽스처 폴더에 둔다.

## 7. 실행

```bash
cd backend
docker build -t harness-sandbox:m2-1 app/m2/sandbox      # 최초 1회
uv run --env-file ../.env python -m app.m2.run prd --input ../docs/m2/fixtures/leave-management/input.md --out ../docs/m2/fixtures/leave-management
uv run --env-file ../.env python -m app.m2.run run --prd ../docs/m2/fixtures/leave-management/prd.json --keep-running
uv run --env-file ../.env python -m app.m2.run run --resume <run dir>   # 실패 지점부터 이어 실행
uv run python -m unittest tests.test_m2                                  # 오프라인 테스트
```

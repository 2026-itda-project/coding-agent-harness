# M2 결정사항과 실행 범위

M2의 제품 요구사항과 공통 개발 기준을 정리한다.

공식 기준은 [M2 방향성 공유본](https://app.notion.com/p/3f7d4def234a80a4b221c99b30c787fb)이다. 이 문서는 해당 내용을 저장소의 개발 기준으로 반영한다. 명시적으로 미정인 입력 양식·실행 환경·3라운드 이후 처리와 구현 세부 계약은 별도 결정하며, 정책 확정이 기능 구현 완료를 의미하지 않는다.

## 1. 목표와 현재 상태

자연어 요구사항을 받아 바로 사용할 수 있는 E2E 서비스를 생성하는 **범용 Harness**를 만든다. 휴가관리는 결과를 검증하는 예시 입력이다. 휴가관리 규칙은 fixture·예시 데이터에만 두고 Spec·Task·Agent 구현에 넣지 않는다.

```text
사용자 입력 → 요구사항·AI 기본값·최소 질문 → Review
→ PRD → Executable Spec → Feature / Task / Dependency
→ Coding Plan → Coding Agent → Build / Test / Acceptance
→ 실패 시 재계획·수정·재검증 → 서비스 내 실행 결과 미리보기
```

기존 M1에는 요구사항 루프, Mock/SnowChat Provider, Review·승인, 규칙 기반 PRD API·UI가 있다. M2는 Claude API와 코드 생성·실행 파이프라인으로 확장한다.

## 2. M2 핵심 결정

| 항목 | M2 기준 |
| --- | --- |
| 프로젝트 정체 | 도메인 범용 Harness. 예시와 제품 로직을 분리 |
| 모델 연결 | 최종 제품은 Claude API. PRD·Spec·Task·코드는 LLM 생성 |
| 코드 책임 | 입력·Context 전달, 응답 정리·검증, 실행 연결. 제품 내용을 코드로 임의 생성·보정하지 않음 |
| 누락 정보 | AI 기본값 우선, 서비스 성격이 달라지는 핵심 결정만 최소 질문 |
| AI 기본값 | 상태·출처를 유지하면서 본문·구현에도 포함. 사용자 명시사항 우선 |
| 결과 제공 | 외부 작업 폴더·로컬 Git으로 내부 관리, 서비스 내 미리보기 제공. 원격 연결·내보내기 없음 |
| 실행 환경 | 로컬 Docker 컨테이너 (#9, PR 리뷰에서 팀 합의) |
| 결과물 범위 | 아래 "생성 서비스 범위" 참고 |

### 생성 서비스 범위

정적 화면 목업이 아니라 주요 흐름이 동작하는 MVP를 만든다. 우선 작게 구현한다.

| 항목 | 기준 |
| --- | --- |
| 데이터 | 새로고침해도 유지된다. 처음에는 LLM이 만든 초기 목데이터가 들어 있다 |
| 기능 규모 | 사용자가 입력한 기능 전부 + 부족한 부분은 AI 기본값으로 보완. 아래 로그인·외부 연동 등 생성 범위 제한은 PRD와 Review에서 명시 |
| 사용자와 역할 | 단일 사용자 또는 역할 전환 버튼. 실제 로그인과 다중 계정은 만들지 않는다 |
| 외부 연동 | 결제, 메일, 지도 등은 실제 연동 없이 모의 동작으로 대체한다. 실제 연동 완료와 구분해 표시·검증한다 |
| 기술 스택 | FastAPI, React, SQLite로 고정한다. LLM이 스택을 고르지 않는다. 빌드한 React를 FastAPI가 함께 제공해 하나의 주소로 미리보기한다 |
| 개발 방식 | PRD는 Sonnet, 구현 명세(Spec)와 개발 계획은 Opus, 실제 개발은 Opus가 조종하는 Sonnet. 전체 코드는 LLM이 만든다 |
| 결과 확인 | 창업자 본인만 서비스 화면에서 미리보기로 확인한다. 외부 공유 링크는 없다 |
| 수정 요청 | 생성 후 새 요구사항을 받아 변경하는 기능은 지원하지 않는다. 생성 전 Review 수정·승인은 유지하며, 최종 Review 결과와 거부·수정되지 않은 AI 기본값으로 생성한다. 생성 중 실패의 자동 수정·재검증은 유지한다 |

범용성은 서비스 도메인에 대한 원칙이다. 이번 생성 대상은 위 스택과 범위를 따르는 웹 MVP이며, 모든 기술 스택·실제 외부 연동을 지원한다는 의미가 아니다. 생성 서비스의 역할 전환은 실제 로그인·다중 계정 인증을 구현한 것으로 판정하지 않는다.

## 3. 입력과 사용자 확인

> **상태: 제안 (2026-10-12, 팀 컨펌 대기).** 컨펌 전까지 구현하지 않는다. 컨펌되면 이 절을 확정으로 바꾸고 #7·#18에서 구현한다.

목적은 기술 창업 아이디어를 가진 비전공자가 수개월의 개발 없이 동작하는 목업으로 아이디어를 테스트하는 것이다. 모두의 창업 양식은 참고 자료이며 그대로 따를 필요는 없다. 입력 항목은 **생성되는 목업을 바꾸는가**와 **비전공자가 쉽게 답할 수 있는가**로 고른다.

| 구분 | 항목 | 이유 |
| --- | --- | --- |
| 필수 | 한 줄 소개 ("누구의 어떤 문제를 어떻게 해결하나요?") | 서비스 정체 |
| 필수 | 해결하려는 문제 (기존 배경·차별점 통합) | 목업에서 보여줄 핵심 기능 |
| 필수 | 아이디어 설명 (자유 서술) | 기능의 원천 |
| 필수 | 누가 쓰나요? (예: 직원, 관리자) | User/Role. 질문 1순위를 입력에서 해소 |
| 필수 | 사용자가 가장 먼저 하는 일 (최대 3개) | Core Flow. 목업 화면 구성의 기준 |
| 선택 | 이 목업으로 확인하고 싶은 것 | 데모 시나리오·Acceptance 기준 |
| 선택 | 돈을 버는 방식 (무료/구독/건당 결제/광고/미정) | 결제·구독 화면 필요 여부만 판단. 실제 결제는 만들지 않음 |
| 선택 | 참고 서비스 | 화면·흐름 기준 |
| 선택 | 이번엔 빼도 되는 것 | Scope 제한 |
| 대안 입력 | 모두의 창업 신청서 붙여넣기 | LLM이 위 항목으로 정리하고 사용자가 확인 |

제외: 분야(LLM 추론), 실현 방법, 사업화 계획, 창업 여부·역량·팀원 수, 멘토 도움, 영상·사진 첨부. 목업 생성에 영향이 없거나 비전공자가 답하기 어렵다.

글자 수 제한, API 필드명·직렬화는 아직 정하지 않았다. 기존 API는 `text` 문자열만 받는다. E1 예시 입력은 컨펌된 항목 구성으로 작성한다.

<details><summary>이전 합의 (2026-10-11, 위 제안으로 대체 검토 중)</summary>

모두의 창업 양식에서 멘토 도움·영상/사진 첨부를 제외한 분야, 한 줄 소개, 배경, 설명, 차별점과 해결, 수익 계획, 실현 방법, 사업화 계획, 창업 여부·역량·팀원 수.

</details>

사용자가 명시하지 않은 내용은 LLM이 일반적인 서비스 관례에 따른 AI 기본값으로 제안한다. 사용자 명시사항은 항상 우선하며 기본값이 이를 바꾸거나 충돌해서는 안 된다. 서비스 성격 자체가 달라지는 핵심 결정만 쉬운 표현으로 질문한다. 기존 3라운드 규칙은 이 최소 질문에 적용한다.

3라운드 뒤에도 핵심 결정이 남았을 때의 구체적인 처리는 미정이다. 기존 M1의 일괄 Proposal 전환을 M2 확정 정책으로 해석하지 않으며, 미결정 상태를 숨기거나 사용자 확정으로 바꾸지 않는다.

| 상태·출처 | 의미 | PRD·구현 처리 |
| --- | --- | --- |
| confirmed | 사용자가 명시하거나 수락한 내용 | 본문·구현에 포함 |
| proposed + AI 출처 | 사용자가 거부·수정하지 않은 AI 기본값 | AI 기본값 표시와 상태·출처를 유지하며 본문·구현에 포함. Assumptions에서도 추적 |
| needs_clarification | 아직 결정되지 않은 내용 | 미결정으로 구분. 해결되지 않은 핵심 결정을 확정 내용으로 위장하지 않음 |

Review와 개별·일괄 수락은 유지한다. 일괄 수락 시 `confirmed`로 바꾸되 AI 출처는 보존한다. **본문 포함 여부와 사용자 확정 여부는 다르다.** M2에서는 미수락 AI 기본값이라는 이유만으로 본문에서 제외하지 않는다.

## 4. LLM과 코드의 역할

| 단계 | 모델 방향 | 생성·판단 대상 |
| --- | --- | --- |
| 요구사항 루프 | Sonnet | 요구사항, AI 기본값, 필요한 질문과 답변 반영 |
| PRD | Sonnet | 제품 수준 PRD |
| Executable Spec | Opus | PRD에 연결된 구현 명세 |
| Feature / Task / Coding Plan | Opus 계획 경로 | 작업 분해, 선후관계와 구현 계획 |
| 코딩 | Sonnet | 생성 서비스 코드 |
| 검증·재계획 | Opus | 실제 실행 결과 분석과 수정 계획 |

Sonnet·Opus는 역할 배정이다. 실제 모델 ID와 Agent 연결 방식은 구현 계약에서 정한다.

최종 제품의 생성·계획·코딩·검증은 Claude API를 사용한다. 기존 FactChat/SnowChat과 규칙 기반 `prd.py`는 현재 M1 상태를 설명하는 자료이며 M2 생성 경로로 확장하지 않는다. Jev는 별도 실험으로 평가하고 제품 연결은 의사결정 이후 후속 범위로 다룬다.

코드는 입력·Context 구성, 모델 호출·응답 trimming, 구조·참조 검증, 상태·실행·로그 연결을 담당한다. trimming은 응답을 계약에 맞게 다루는 처리이며, 모델이 빠뜨린 제품 내용·ID·의존관계를 코드가 임의로 만들어 성공 처리하는 것을 뜻하지 않는다.

최소 검증 대상:

- 스키마와 필수 필드
- 원본 Requirement / Spec / Task ID 참조
- 중복 ID와 잘못된 참조
- Task 순환 의존관계
- AI 기본값의 상태·출처 보존과 사용자 명시사항 우선 여부

구조 검증은 코드가 수행하고, 제품 내용의 충돌·충족 판단은 LLM 검토와 실제 Acceptance 검증으로 확인한다. 실패한 산출물은 통과와 구분하고 오류를 남긴다. 내용 보완·재생성이 필요하면 오류를 LLM에 전달한다. Mock이나 fixture 결과를 실제 모델 결과로 대체 보고하지 않는다.

## 5. 생성물 관리와 실행 결과

- 생성 코드는 Harness 코드베이스 밖의 지정 작업 폴더에서 만들고 실행한다. 구체적인 디렉터리 규칙은 E5에서 정한다.
- 해당 폴더에 로컬 Git을 사용하고 원격에 연결하지 않는다.
- Task 단위 커밋과 변경 파일·diff를 기록해 실행 이력과 실패 시 복구의 근거로 사용한다.
- 로컬 커밋과 검증 통과는 별개다. 검증 결과와 연결해서 해석한다.
- 최소 Git 이력·복구와 고급 Checkpoint/Rollback은 구분한다. 고급 복구 체계는 이번 범위 밖이다.
- 사용자에게는 서비스 화면에서 실행 결과 미리보기를 제공한다. 포트를 iframe이나 프록시로 연결하는 구체적인 방식은 #13에서 정한다.
- 결과물 내보내기, 원격 저장소 생성·Push 기능은 만들지 않는다. 이 제한은 생성 서비스의 제품 기능에 대한 것으로 Harness 개발 저장소의 협업 절차와 구분한다.

실행 환경은 로컬 Docker로 한다. Agent가 실행하는 명령과 생성 앱의 빌드·실행·미리보기 서버를 작업 폴더만 마운트한 컨테이너에서 돌리고, 자원·권한 제한과 정리 방식은 [E2E 계약](../m2/e2e-contract.md) 5절을 따른다(#9, PR 리뷰에서 팀 합의). 클라우드 배포는 기본 E2E의 필수 선행조건이 아니다.

## 6. 기본 E2E 개발 단계

고정 PRD에서 시작해 생성 코드가 실행되는 기본 E2E를 연결한다.

| 작업 | 산출물·완료 판단 | 선행 |
| --- | --- | --- |
| [E1 / #1](https://github.com/2026-itda-project/coding-agent-harness/issues/1) | 양식 기반 예시 입력, 고정 PRD, 범용 입력 계약, Acceptance Checklist, fixture 검증 | 기존 계약·예시 확인 |
| [E2 / #2](https://github.com/2026-itda-project/coding-agent-harness/issues/2) | LLM Spec 생성, 요구사항 추적·구조 검증 | E1 |
| [E3 / #3](https://github.com/2026-itda-project/coding-agent-harness/issues/3) | Opus 작업 분해·Dependency·Coding Plan, 순차 실행 순서 | E2 |
| [E4 / #4](https://github.com/2026-itda-project/coding-agent-harness/issues/4) | 현재 Task에 필요한 최소 Context와 출처 | E3 |
| [E5 / #5](https://github.com/2026-itda-project/coding-agent-harness/issues/5) | Sonnet Agent, 작업 폴더·로컬 Git, 파일·명령 실행과 diff 기록 | E3·E4 |
| [E6 / #6](https://github.com/2026-itda-project/coding-agent-harness/issues/6) | 실제 연결·생성 코드 최소 실행·재현 기록 | E2~E5 |

고정 PRD는 출처와 검증 결과를 기록하고 동일 입력으로 반복 실행할 수 있어야 한다. Mock·fixture 검증과 실제 모델 호출을 구분한다. 기본 연결 완료와 최종 M2 자동화 완료를 구분한다.

## 7. 이후 범위와 최종 완료 기준

- [#7](https://github.com/2026-itda-project/coding-agent-harness/issues/7)·[#8](https://github.com/2026-itda-project/coding-agent-harness/issues/8): 양식 입력·AI 기본값 정책, Sonnet Requirement Loop·PRD 연결
- [#9](https://github.com/2026-itda-project/coding-agent-harness/issues/9)·[#10](https://github.com/2026-itda-project/coding-agent-harness/issues/10)·[#11](https://github.com/2026-itda-project/coding-agent-harness/issues/11): Sandbox Build/Test, Opus Verification, 재계획·수정·재검증
- [#12](https://github.com/2026-itda-project/coding-agent-harness/issues/12)·[#13](https://github.com/2026-itda-project/coding-agent-harness/issues/13): Run 지표·Task 커밋/diff 연결, 진행 상태·실행 결과 미리보기
- [#14](https://github.com/2026-itda-project/coding-agent-harness/issues/14)·[#15](https://github.com/2026-itda-project/coding-agent-harness/issues/15): 중간 피드백 반영, 최종 통합·DoD 검증
- [#16](https://github.com/2026-itda-project/coding-agent-harness/issues/16)·[#17](https://github.com/2026-itda-project/coding-agent-harness/issues/17): Jev 실험 기획·수행·도입 판단
- [#18](https://github.com/2026-itda-project/coding-agent-harness/issues/18): 기존 요구사항·PRD 화면의 표현·정보 구성 개선. AI 기본값과 다음 행동을 쉽게 설명

Jev는 Prompt-only Baseline과 동일 조건으로 비교하고 과잉 Blocking, 핵심 Gap 회수율, 질문 라운드, Token, Latency, 비용을 평가한다. 판단 기준을 실험 전에 정하고 도입·제한적 도입·보류를 결정한다. 기본 E2E와 독립적으로 진행한다.

실험 대상은 AI 기본값으로 진행할 부분과 사용자에게 꼭 물어야 할 핵심 결정을 구분하는 판단이다. M1 평가 사례는 재사용할 수 있지만 기대 판단은 같은 M2 정책으로 검토하고, 기존 M1 결과는 별도로 보존한다. 고정 사례로 Jev 판단을 먼저 확인한 뒤 같은 M2 정책·입력 조건에서 Sonnet 단독과 Sonnet+Jev를 비교한다. 고정 사례 실험만으로 전체 E2E 개선을 주장하지 않으며, 질문 감소가 핵심 결정 누락으로 이어지면 개선으로 보지 않는다. 평가 기준·실행 한도는 실험 전에 정하고 제품 연결은 결과 검토 후 결정한다.

최종 M2는 다음 증거가 있을 때 완료한다.

- [ ] Claude로 생성한 PRD → Spec → Task/Dependency → 생성 코드가 추적된다.
- [ ] Coding Agent가 실제 파일·명령을 처리하고 Task별 로컬 커밋·변경 기록이 남는다.
- [ ] Sandbox가 Build·Test를 실행하고 Acceptance 충족을 확인한다.
- [ ] 실패 사례에서 Opus 재계획 → Sonnet 수정 → 재검증을 재현한다.
- [ ] 사람이 직접 코드를 작성하지 않은 실행 가능한 MVP를 확인한다.
- [ ] 시간·Token·비용·Retry·사람 개입·성공 여부·실패 원인을 Run 단위로 확인한다. 미확인 값을 0으로 만들지 않는다.
- [ ] 비개발자가 입력·핵심 질문·기능 확인·생성 요청·실행 결과를 이해할 수 있다.
- [ ] 생성 서비스가 FastAPI·React·SQLite를 사용하고 React 빌드 결과를 FastAPI가 함께 제공한다.
- [ ] 초기 예시 데이터가 있으며 새로고침 후에도 데이터가 유지된다.
- [ ] 역할 전환·외부 연동의 모의 동작이 실제 인증·실제 연동 완료로 보고되지 않는다.

범용 Multi-Agent, 병렬 Task, 장기 Memory, 고급 Context Compression, 완성형 Delta Spec, 고급 Checkpoint/Rollback, 복잡한 운영 Dashboard, Production SaaS 운영은 범위 밖이다.

## 8. 기술 미정사항

| 미정사항 | 정할 위치·조건 |
| --- | --- |
| 실제 Opus/Sonnet 모델 ID | 모델 호출 설정에서 명시 |
| Coding Agent 도구 실행 방식 | E5 구현에서 결정. 모델은 Opus가 조종하는 Sonnet |
| 입력 항목 구성 | 3절 제안의 팀 컨펌 |
| 3라운드 이후 핵심 미결정 처리 | 자동 확정하지 않는 원칙을 유지하며 후속 정책 결정 |
| 입력 글자 수 제한·API 계약 | 컨펌 후 #7·#8·#18 |
| 작업 폴더 경로·미리보기 연결 | E5·E6 및 #13에서 결정 |
| 구체적인 Retry 한도·중단·복구 정책 | #11에서 결정 |
| Jev 도입·적용 범위 | #16·#17 실험 결과로 판단 |


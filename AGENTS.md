# AGENTS.md

## Scope Interpretation

이 문서는 프로젝트 공통 원칙, 현재 M2 개발 기준과 기존 M1 Context를 설명합니다.
다만, 문서에 기술된 모든 기능이 현재 작업자의 구현 범위라는 의미는 아닙니다.

Coding Agent는 작업을 시작하기 전에 반드시 현재 Task의 Owner, 구현 범위, 선행 단계, 후속 Handoff 지점을 확인해야 하며, 현재 Task Scope가 M1 전체보다 좁은 경우 현재 Task Scope를 우선합니다.

현재 작업자의 작업 이후 진행할 다른 담당자의 후속 기능이 남아 있는 경우, Interface와 Handoff를 고려하되 후속 기능을 선행 구현하지 않아야 합니다.

## Current Milestone: M2

현재 개발 기준은 [M2 결정사항과 실행 범위](docs/decisions/m2-development-direction.md)입니다. 아래 M1 기획·담당·초기 구현 제안은 기존 단계의 맥락이며, M2에 명시적으로 변경된 사항은 이 절과 연결된 결정문을 우선합니다. 계획은 실제 구현 완료를 뜻하지 않습니다.

공식 정책 원문은 [M2 방향성 공유본](https://app.notion.com/p/3f7d4def234a80a4b221c99b30c787fb)입니다. 원문에서 미정으로 남긴 입력 양식·실행 환경을 임의로 확정하지 않습니다. 3라운드 이후 핵심 결정 처리는 PR #22 리뷰의 팀 합의에 따른 임시 결정(7.3)을 적용합니다.

- 범용 Harness를 개발합니다. 휴가관리 예시는 fixture·예시 데이터로만 두며 Spec·Task·Agent에 도메인 전용 로직을 넣지 않습니다.
- M2 최종 제품의 생성·계획·코딩·검증 호출은 Claude API를 사용합니다. 요구사항과 PRD는 Sonnet, Spec과 Coding Plan, 검증, 재계획은 Opus이며 실제 코딩은 Opus가 조종하는 Sonnet이 합니다. 생성 서비스 스택은 FastAPI, React, SQLite로 고정합니다. 실제 모델 ID와 권한은 확인이 필요합니다. Jev는 별도 비교 실험이며 기본 경로의 선행조건이 아닙니다.
- PRD·Spec·Task·코드는 LLM이 생성합니다. 코드는 입력·Context 전달, 응답 정리, 스키마·필수 필드·ID 참조·중복·순환 검증과 실행을 담당합니다. 잘못된 산출물을 제품 내용의 임의 생성·보정으로 통과시키지 않습니다.
- 입력 항목은 목업 생성에 필요한 항목 중심으로 재구성하는 제안이 팀 컨펌 대기 중입니다(결정문 3절). 컨펌 전에는 입력 계약을 구현하지 않습니다.
- AI 기본값과 최소 질문 정책은 7.4·8·9·17장을 따릅니다. `proposed`·AI 출처를 유지하면서 PRD 본문과 구현 범위에 포함하고, 사용자 명시사항을 우선합니다. Review·일괄 수락은 유지합니다.
- Mock·fixture 검증과 실제 모델 E2E 실행 결과를 구분합니다.
- 생성물은 Harness 밖 작업 폴더에서 로컬 Git으로 관리하고 원격에 연결하지 않습니다. Task 단위 커밋·변경 파일·diff를 남깁니다. 사용자에게는 서비스 내 실행 결과 미리보기를 제공하며 내보내기는 범위 밖입니다.
- 구체적인 실행 환경은 미정입니다. Sandbox·Verification·Retry는 M2 개발 범위에 포함합니다.
- 생성 서비스는 초기 예시 데이터와 새로고침 후 데이터 유지를 지원하며, React 빌드 결과를 FastAPI가 함께 제공합니다. 실제 로그인·다중 계정 대신 단일 사용자 또는 역할 전환을 사용하고 외부 연동은 모의 동작임을 표시합니다. 외부 공유 링크는 제공하지 않습니다.
- M2 사용자 화면은 입력, 질문, 검토, 생성, 완료의 단계형이며 검토 뒤 '생성' 버튼 하나로 PRD부터 빌드와 점검까지 진행합니다. PRD, 인계 JSON, Gap 관리, AC 편집, 비용 같은 내부 산출물은 사용자에게 보여 주지 않고 내부 기록으로 유지합니다. 아래 11장 M1 UI 요구사항은 M1 Demo 기준입니다.
- 생성 후 새 요구사항에 따른 변경은 범위 밖입니다. 생성 전 Review 수정·승인과 생성 중 자동 오류 수정·재검증은 유지합니다. 미수락 AI 기본값도 거부·수정되지 않았다면 상태·출처를 유지하며 생성에 포함합니다.

## 1. Project Overview

이 저장소는 사용자가 입력한 자연어 요구사항을 분석하고, 필요한 정보를 보완한 뒤 구조화된 개발 산출물로 연결하는 **AI Agent Harness**를 구현하기 위한 프로젝트입니다.

전체 프로젝트가 지향하는 흐름은 다음과 같습니다.

```text
Natural Language Requirements
        ↓
PRD
        ↓
Development Spec
        ↓
Development Tasks
        ↓
Coding Agent
        ↓
Working Web SaaS
```

기존 **M1**에서는 이 중 자연어 요구사항부터 PRD까지의 흐름을 구현했습니다. 현재 **M2**에서는 PRD 이후 실행·검증·MVP 미리보기까지 확장합니다.

M1의 핵심은 자연어를 곧바로 PRD 형식으로 변환하는 것이 아닙니다.

사용자가 제공한 불완전한 요구사항에서 중요한 누락 또는 모호한 내용을 발견하고, 필요한 내용을 추가로 확인하고, 사용자와 요구사항을 확정한 뒤 구조화된 PRD로 연결하는 Workflow를 검증하는 것이 목적입니다.

---

# Part 1. Confirmed M1 Product Decisions

이 Part의 내용은 기존 M1 Demo 기획의 확정사항입니다. M2 변경사항은 상단 Current Milestone과 7.4 등 명시된 변경 절을 함께 읽습니다.

구현 과정에서 임의로 변경하지 않습니다.

변경이 필요한 경우 팀 논의를 통해 기획 자체를 수정해야 합니다.

---

## 2. M1 Scope

### 2.1 In Scope

M1에서는 다음 흐름을 구현합니다.

```text
1. 자연어 서비스 요구사항 입력
2. 초기 요구사항 구조화
3. 누락되거나 모호한 요구사항 식별
4. 필요한 추가 질문 생성
5. 사용자 답변 반영
6. 정리된 요구사항 검토 및 수정
7. 구조화된 PRD 생성
8. 생성된 PRD 확인 및 검증
```

### 2.2 Out of Scope

다음 내용은 M1 이후 단계에서 다룹니다.

```text
DB Schema 설계
API Endpoint 상세 정의
Framework 및 기술 스택 자동 선정
구체적인 화면 Component 설계
Development Task 생성
Task 간 의존관계 구성
Coding Agent 실행
실제 Web SaaS 구현
복잡한 프로젝트 관리 Dashboard
```

M1에서는 **Requirements → PRD Workflow 자체를 검증하는 것**에 집중합니다.

---

## 3. PRD and Development Spec Boundary

PRD는 다음 질문에 답해야 합니다.

> 무엇을 만들어야 하는가?
> 어떤 조건에서 동작해야 하는가?

PRD에는 제품 수준의 요구사항을 정의합니다.

```text
Product Purpose
Users / Roles
Scope
User Flow
Functional Requirements
Business Rules
Constraints / NFR
Acceptance Criteria
```

구체적인 구현 방법은 Development Spec으로 분리합니다.

```text
화면 상세 구조
DB Schema
API Endpoint
Framework / 기술 스택
구체적인 권한 구현
Component 구조
Development Task 구성
```

PRD의 완료 기준은 다음과 같습니다.

> Development Spec 단계에서 제품 요구사항 자체를 다시 추측하지 않아도 되는 상태

---

## 4. Representative Demo Scenario

M1의 대표 Demo Scenario는 **사내 휴가관리 Web SaaS**입니다.

기본 입력 예시는 다음과 같습니다.

> 직원들이 휴가를 신청하고 관리자가 승인할 수 있는 사내 휴가관리 서비스를 만들어줘.

이 입력은 의도적으로 불완전하게 구성되어 있습니다.

추가 확인이 필요한 내용의 예시는 다음과 같습니다.

```text
어떤 사용자가 존재하는가
어떤 종류의 휴가를 지원하는가
누가 휴가를 승인할 수 있는가
승인 절차가 몇 단계인가
잔여 연차는 언제 차감되는가
신청 수정 및 취소가 가능한가
승인 이후 취소가 가능한가
예외 상황을 어떻게 처리하는가
```

이를 통해 시스템이 사용자의 문장을 단순히 확장하는 것이 아니라, 실제 서비스 동작을 결정하기 위해 필요한 정보를 식별하고 보완할 수 있는지 확인합니다.

가능하면 이후 M2/M3에서도 동일한 Demo Scenario를 사용하여 산출물 간 추적 관계를 유지합니다.

M2에서는 이 시나리오를 fixture와 예시 데이터로만 사용합니다. 서비스 도메인에 대한 판단과 생성은 LLM이 수행하며 Harness의 Spec·Task·Agent 코드에 휴가관리 규칙을 하드코딩하지 않습니다.

---

## 5. M1 Workflow

확정된 M1 Workflow는 다음과 같습니다.

```text
Natural Language Requirement
        ↓
Requirement Extraction
        ↓
Gap Analysis
        ↓
Blocking Issue 존재?
        │
        ├─ Yes
        │    ↓
        │ Question Generation
        │    ↓
        │ User Answer
        │    ↓
        │ Requirement Update
        │    ↓
        │ Gap Analysis
        │
        └─ No
             ↓
      Requirement Review
             ↓
       PRD Generation
             ↓
       PRD Validation
             ↓
          Final PRD
```

M1에서는 여러 Agent를 먼저 구현하는 것을 목표로 하지 않습니다.

하나의 LLM을 사용하더라도 다음 단계의 책임과 입력 및 출력은 구분합니다.

```text
Requirement Extraction
Gap Analysis
Question Generation
Requirement Update
PRD Generation
PRD Validation
```

향후 필요하면 각 단계를 독립 Agent 또는 Module로 분리할 수 있도록 인터페이스를 구분합니다.

---

## 6. Requirement Extraction

Requirement Extraction 단계에서는 사용자가 실제로 입력한 내용을 구조화합니다.

최초 입력에서 최소한 다음 종류의 정보를 식별할 수 있어야 합니다.

```text
사용자가 명시한 기능
사용자 및 역할
주요 업무 흐름
업무 규칙
제약조건
아직 결정되지 않은 내용
```

이 단계에서 사용자가 말하지 않은 내용을 임의로 확정 요구사항으로 추가하지 않습니다.

---

## 7. Gap Analysis and Clarification

### 7.1 Clarification Priority

모든 누락 정보를 사용자에게 질문하지 않습니다.

서비스 동작 또는 구현 범위에 큰 영향을 주는 내용을 우선적으로 확인합니다.

우선 확인 영역은 다음 8개입니다.

```text
1. User / Role
2. Core Business Flow
3. Permission / Approval
4. State Change
5. Modify / Cancel Policy
6. Exception Handling
7. Service Scope
8. Required External Integration
```

서비스 이름이나 세부 UI 표현처럼 구현 결과에 큰 영향을 주지 않는 항목은 기본적으로 필수 Clarification 대상에서 제외합니다.

### 7.2 Blocking

각 미결정 Requirement에는 `blocking` 여부를 둘 수 있습니다.

Gap Analysis는 위 8개 우선 영역을 중심으로 Blocking 여부를 판단합니다.

서비스의 핵심 동작을 결정하기 위해 반드시 확인되어야 하는 내용은 Blocking Issue로 처리합니다.

### 7.3 Clarification Termination

Clarification은 다음 조건을 따릅니다.

```text
Blocking Issue가 모두 해소됨
→ Requirement Review 단계로 이동

Blocking Issue가 존재함
→ Clarification 수행
```

Clarification 질문 라운드는 최대 **3회**로 제한합니다.

3회 이후에도 Blocking Issue가 남아 있는 경우 해당 항목을 **AI 기본값 Proposal**로 전환하고 Requirement Review 화면에서 사용자가 확인할 수 있도록 합니다.

M2에서도 PR #22 리뷰의 팀 합의에 따른 임시 결정으로 최대 3라운드 뒤 남은 Blocking을 AI Proposal로 전환하고 Review에서 확인합니다. 전환된 항목은 `proposed`와 AI 출처를 유지하며, 원본 Gap·대상 Requirement·전환 사유·라운드를 별도로 기록합니다. 전환만으로 사용자 확정 상태가 되지는 않습니다. 강제 전환 항목의 별도 표시·추가 확인 방식은 후속 합의 대상입니다.

사용자가 AI Proposal을 수락하면 해당 Blocking Issue는 해소된 것으로 처리합니다.

### 7.4 M2 변경: AI 기본값 우선 (대표님 피드백 반영)

M2의 주 사용자는 IT 비전공자이며, Gap 분석 결과에 직접 답하는 것을 어려워할 수 있습니다.

따라서 M2부터는 다음 원칙을 우선합니다.

```text
사용자가 명시하지 않은 내용
→ 질문하지 않고 AI가 일반적인 서비스 관례에 맞는 기본값으로 정한다 (proposed)

사용자가 명시한 내용
→ 그대로 따른다. AI 기본값이 이를 바꾸거나 충돌해서는 안 된다

AI 기본값으로 정할 수 없는 핵심 결정
(사용자 의도에 따라 서비스 성격 자체가 달라지는 경우)
→ 이때만 쉬운 표현으로 최소한의 Clarification 질문을 한다
```

위 7.1~7.3의 Blocking·라운드 규칙은 이 최소 질문에만 적용합니다.

---

## 8. Requirement State

Requirement는 단순 문자열 목록으로 관리하지 않습니다.

각 Requirement는 현재 상태와 생성 출처를 함께 관리합니다.

기본 상태는 다음 세 가지입니다.

```text
confirmed
needs_clarification
proposed
```

### confirmed

사용자가 최초 입력 또는 Clarification 답변을 통해 명시적으로 확정한 내용입니다.

또는 사용자가 AI Proposal을 확인하고 수락한 내용입니다.

### needs_clarification

서비스 동작에 영향을 주지만 아직 결정되지 않은 내용입니다.

### proposed

사용자가 명시하지 않은 내용에 대해 시스템이 제시한 AI Proposal입니다.

AI Proposal은 사용자 확인 없이 자동으로 confirmed 상태가 되어서는 안 됩니다.

단, M2부터 `proposed`는 "사용자가 거부·수정하지 않은 AI 기본값"으로서 일반 Requirement처럼 PRD 본문과 구현 범위에 포함합니다(7.4). `status`와 `source`는 AI 기본값임을 추적하기 위해 유지합니다.

---

## 9. AI Proposal Rules

Requirement Review 화면에서는 AI Proposal을 사용자가 확인할 수 있어야 합니다.

사용자는 Proposal을 개별적으로 수락하거나 여러 Proposal을 일괄 수락할 수 있습니다.

AI Proposal을 사용자가 수락하면:

```text
status = confirmed
```

로 변경합니다.

그러나 AI가 생성한 내용이라는 `source` 정보는 유지합니다.

사용자가 확인하지 않은 `proposed` Requirement는 삭제하지 않습니다.

최종 PRD의 **Assumptions** 항목에 가정으로 표시하여 포함합니다.

M2부터는 Assumptions에 표시하는 것과 별개로, 해당 내용을 PRD 본문(Functional Requirements, Business Rules 등)과 구현 범위에도 포함하고 AI 기본값임을 표시합니다(7.4).

---

## 10. Requirement Review

Clarification이 끝난 뒤 바로 PRD를 생성하지 않습니다.

먼저 현재까지 정리된 Requirement를 사용자에게 보여주는 Review 단계를 거칩니다.

Workflow는 다음과 같습니다.

```text
Clarification Complete
        ↓
Requirement Review
        ↓
User Edit / Approval
        ↓
PRD Generation
```

Review 화면에서는 최소한 다음 상태를 구분해서 보여줍니다.

```text
Confirmed Requirements
Needs Clarification
AI Proposals
```

사용자는 정리된 Requirement를 직접 수정할 수 있어야 합니다.

AI Proposal을 개별 또는 일괄 수락할 수 있어야 합니다.

사용자가 현재 Requirement를 검토하고 승인한 이후에 PRD를 생성합니다.

---

## 11. M1 Demo UI Requirements

M1 UI는 전체 Workflow를 검증할 수 있는 최소 수준으로 구현합니다.

복잡한 Dashboard 또는 프로젝트 관리 기능은 필요하지 않습니다.

### Input Area

```text
최초 자연어 요구사항 입력
Clarification 질문에 대한 사용자 응답
```

### Requirement Area

```text
Confirmed Requirements
Needs Clarification
AI Proposals
```

AI Proposal에 대해서는 개별 수락 및 일괄 수락 흐름을 지원합니다.

### Result Area

```text
정리된 Requirement Review
사용자 수정 및 승인
PRD Preview
```

UI의 시각적 완성도보다 Requirement State와 Workflow가 올바르게 동작하는 것을 우선합니다.

---

## 12. Requirement Data Contract

Requirement는 내부적으로 구조화된 데이터로 관리합니다.

현재 확정된 기본 필드는 다음과 같습니다.

```text
Requirement

project_id
id
category
description
status
source
blocking
acceptance_criteria
```

필드 의미는 다음과 같습니다.

```text
project_id
프로젝트 식별자

id
Requirement 식별자

category
Requirement 유형

description
Requirement 내용

status
confirmed / needs_clarification / proposed

source
최초 사용자 입력 / Clarification 답변 / AI 제안 등 생성 출처

blocking
PRD 생성 전 반드시 결정해야 하는 내용인지 여부

acceptance_criteria
해당 Requirement의 완료 조건
```

### Important

`category`의 구체적인 Enum 목록은 현재 제품 기획에서 확정하지 않았습니다.

구현 과정에서 필요한 초기 Enum을 제안할 수 있지만, 이를 확정된 제품 요구사항으로 취급하지 않습니다.

---

## 13. Gap Data Contract

Gap Analysis 단계 자체와 Blocking 판정 규칙은 확정되어 있습니다.

그러나 `Gap` 객체의 구체적인 필드 Schema는 현재 제품 기획에서 확정하지 않았습니다.

따라서 다음과 같은 구조를 구현할 수는 있지만 **초기 구현 제안**으로 취급해야 합니다.

```json
{
  "gap_id": "GAP-001",
  "category": "approval",
  "description": "승인 주체가 결정되지 않았다.",
  "blocking": true,
  "question": "휴가 신청은 누가 승인하나요?",
  "ai_proposal": "직속 관리자가 승인"
}
```

팀이 실제 구현을 진행하면서 더 적합한 Contract를 합의할 수 있습니다.

Requirement Contract와의 일관성과 Clarification 단계로의 전달 가능성을 우선합니다.

---

## 14. PRD Output Structure

M1에서 생성하는 PRD에는 다음 Section을 포함합니다.

```text
1. Product Overview
2. Scope
3. Users / Roles
4. User Scenario / User Flow
5. Functional Requirements
6. Business Rules
7. Non-functional Requirements / Constraints
8. Acceptance Criteria
9. Assumptions
10. Open Issues
```

### Product Overview

다음을 포함합니다.

```text
서비스 개요
해결하려는 문제
제품 목표
```

### Scope

```text
In Scope
Out of Scope
```

### Users / Roles

주요 사용자 유형과 사용자별 역할 및 목적을 정리합니다.

### User Scenario / User Flow

주요 사용 시나리오와 핵심 업무 흐름을 정의합니다.

### Functional Requirements

제품 기능을 개별 Requirement로 관리합니다.

### Business Rules

서비스 동작을 결정하는 업무 규칙을 정의합니다.

### Non-functional Requirements / Constraints

사용자가 명시한 성능, 보안, 운영 환경 등의 제약조건을 정리합니다.

### Acceptance Criteria

핵심 Requirement가 충족되었는지 판단할 수 있는 사용자 관점의 완료 조건을 정의합니다.

### Assumptions

사용자가 명시적으로 확정하지 않았지만 다음 단계 진행을 위해 사용한 AI Proposal 또는 기본값을 가정으로 표시합니다.

### Open Issues

PRD 생성 시점까지 결정되지 않았으며 이후 별도 확인이 필요한 내용을 관리합니다.

---

## 15. Requirement IDs and Traceability

후속 단계에서 Requirement를 추적할 수 있도록 주요 PRD 항목에 ID를 부여합니다.

```text
ROLE-    Role
US-      User Scenario / User Story
FR-      Functional Requirement
RULE-    Business Rule
AC-      Acceptance Criteria
NFR-     Non-functional Requirement
```

PRD는 제품 요구사항의 **Source of Truth**입니다.

Development Spec은 제품 요구사항을 새로 정의하지 않고 PRD의 ID를 참조합니다.

---

## 16. Permission Boundary

권한 관련 내용은 PRD와 Development Spec 사이에서 다음과 같이 분리합니다.

### PRD

```text
ROLE
사용자 역할

RULE
누가 무엇을 할 수 있는가
```

### Development Spec

```text
AUTH
해당 ROLE/RULE을 어느 API 또는 화면에서 어떻게 검사하는가
```

즉 Development Spec은 PRD의 ROLE/RULE을 참조하여 구현 방법을 정의합니다.

---

## 17. PRD Validation

PRD Generation과 PRD Validation은 별도 단계입니다.

Final PRD를 출력하기 전에 최소한 다음 내용을 확인합니다.

```text
미해결 Blocking Requirement가 남아 있지 않은가

사용자가 확정한 내용과 충돌하는 Requirement가 없는가

사용자가 승인하지 않은 AI Proposal이
confirmed Requirement로 포함되지 않았는가
(M2: PRD 본문에 포함된 AI 기본값은 출처가 AI 기본값으로 표시되어 있는가)

필수 PRD Section이 모두 존재하는가

주요 Functional Requirement에
Acceptance Criteria가 정의되어 있는가
```

Validation에 실패하면 바로 Final PRD로 출력하지 않습니다.

PRD를 수정한 뒤 다시 Validation을 수행합니다.

M2에서는 수정할 제품 내용을 코드가 임의로 채우지 않습니다. 스키마·필수 필드·ID 참조·중복·순환 의존을 최소 검증하고, 실패 결과를 통과한 산출물과 구분합니다. 재생성이 필요한 경우 오류를 LLM에 전달합니다. 재시도 정책의 구체적인 값은 해당 이슈에서 정합니다.

---

## 18. Common Execution Logging

M1부터 이후 단계까지 프로젝트 단위 실행 정보를 연결할 수 있도록 공통 로그 구조를 유지합니다.

다음 필드를 기록할 수 있는 구조를 둡니다.

```text
project_id
run_id
stage
model
input_tokens
output_tokens
cost
started_at
ended_at
```

M1 전용 지표는 별도로 관리할 수 있습니다.

예:

```text
Clarification 질문 수
대화 Turn 수
사용자 수정량
미승인 AI 가정 수
```

Logging 구현 때문에 핵심 Workflow 구현이 지연되어서는 안 됩니다.

---

## 19. Evaluation Plan

M1은 Workflow가 동작하는지만 확인하는 것이 아니라, Requirement 보완 과정이 실제 PRD 품질에 어떤 영향을 주는지도 평가할 수 있도록 설계합니다.

### Requirement Quality

다음을 확인합니다.

```text
중요한 누락 Requirement를 식별했는가
모호하거나 충돌하는 내용을 발견했는가
필요한 Clarification이 이루어졌는가
사용자 답변이 올바르게 반영되었는가
```

### PRD Quality

```text
필수 Requirement가 누락되지 않았는가
사용자 입력과 충돌하는 내용이 없는가
사용자가 승인하지 않은 AI 가정이 확정 Requirement로 들어가지 않았는가
핵심 기능에 Acceptance Criteria가 존재하는가
```

### Interaction and Cost

```text
Clarification 질문 수
대화 Turn 수
사용자 수정량
PRD 생성 소요시간
Token 사용량
API 비용
```

---

## 20. Baseline Comparison

가능한 경우 동일한 초기 Requirement를 사용하여 다음 두 방식을 비교합니다.

### Baseline

```text
Natural Language Requirement
        ↓
Direct PRD Generation
```

### M1 Workflow

```text
Natural Language Requirement
        ↓
Requirement Analysis
        ↓
Clarification
        ↓
Requirement Review
        ↓
PRD Generation
```

비교할 수 있는 항목은 다음과 같습니다.

```text
핵심 Requirement 누락 수
사용자 입력과 충돌하는 내용
미승인 AI 가정 수
최종 PRD 사용자 수정량
Clarification 질문 수
대화 Turn 수
PRD 생성 소요시간
Token / API 비용
```

핵심 Requirement 누락 수를 비교하기 위해 **휴가관리 Demo Scenario용 필수 Requirement Checklist를 사람이 사전에 작성**합니다.

LLM Output 변동성을 고려해 실제 비교 실험을 수행하는 경우:

```text
Baseline 최소 3회
M1 Workflow 최소 3회
```

실행하여 비교합니다.

### Important

Baseline 비교 실험은 **M1 Definition of Done이 아닙니다.**

M1 일정에서는 핵심 Workflow 구현을 우선합니다.

Baseline 비교는 가능한 경우 추가 실험으로 수행합니다.

---

## 21. M1 Definition of Done

M1은 다음 흐름이 하나의 Demo로 동작하면 기본 완료로 봅니다.

```text
1.
사용자가 자연어로 서비스 Requirement를 입력할 수 있다.

2.
시스템이 입력된 Requirement를 구조화할 수 있다.

3.
누락되거나 모호한 핵심 Requirement를 식별할 수 있다.

4.
필요한 경우 사용자에게 Clarification Question을 제시할 수 있다.

5.
사용자의 답변을 기존 Requirement에 반영할 수 있다.

6.
사용자가 정리된 Requirement를 확인하고 수정할 수 있다.

7.
확정된 Requirement를 기반으로 구조화된 PRD를 생성할 수 있다.

8.
생성된 PRD의 누락 및 충돌 여부를 검증할 수 있다.

9.
Final PRD를 이후 Development Spec 단계의 입력으로 사용할 수 있는 형태로 출력할 수 있다.
```

M1 핵심 산출물은 다음과 같습니다.

> 자연어 Requirement를 바로 PRD로 변환하는 기능이 아니라, Requirement를 분석하고 보완하고 확정한 뒤 구조화된 PRD로 연결하는 Workflow Demo

---

# Part 2. Engineering Guidelines

이 Part는 확정된 제품 요구사항이 아니라 저장소를 안정적으로 공동 개발하기 위한 Engineering Rule입니다.

제품 기획과 충돌할 경우 현재 마일스톤의 확정 Product Decision을 우선합니다. M2는 상단 Current Milestone과 연결된 결정문, M1 재현은 Part 1의 기존 규칙을 기준으로 합니다.

---

## 22. Agent Working Rules

Coding Agent는 코드를 작성하기 전에 먼저 다음을 확인합니다.

```text
현재 사용자의 작업 요청
이 AGENTS.md
현재 Repository 구조
관련 Schema
관련 Prompt
관련 API Contract
테스트 코드
README
관련 문서
```

작업과 관련된 Notion, Google Drive, Slack 등의 외부 자료가 현재 환경에서 실제로 접근 가능하면 추가 Context로 사용할 수 있습니다.

접근할 수 없는 외부 문서의 내용을 추측하지 않습니다.

---

## 23. Source Priority

정보가 충돌하는 경우 다음 순서를 기준으로 판단합니다.

```text
1. 현재 사용자가 명시적으로 요청한 작업
2. 현재 디렉터리에 적용되는 AGENTS.md (M2 변경 절과 연결된 결정문 포함)
3. 최신 확정 PRD 또는 Product Decision
4. 최신 Development Spec
5. 확정된 Schema / API Contract
6. Test
7. 실제 구현 코드
8. README 및 기타 문서
```

실질적인 구현 방향이 달라질 정도의 충돌이 있다면 임의로 결정하지 않고 충돌 내용을 명시합니다.

---

## 24. Development Principles

다음 원칙을 따릅니다.

```text
기존 Repository 구조와 Convention을 우선한다.

현재 작업과 무관한 대규모 Refactoring을 하지 않는다.

이미 존재하는 기능을 중복 구현하지 않는다.

단계 간 입력 및 출력 Contract를 명확하게 유지한다.

Mock과 실제 LLM 구현을 명확하게 구분한다.

API Key, Token, Secret을 Repository에 Commit하지 않는다.

Prompt와 Schema를 Version Control 대상으로 관리한다.

새로운 Library를 추가하기 전에 기존 Dependency로 해결 가능한지 확인한다.

변경 후 가능한 범위에서 Test 또는 실행 검증을 수행한다.
```

---

## 25. LLM Implementation Principles

LLM에게 전체 Workflow를 한 번에 수행하도록 하는 거대한 Prompt를 우선하지 않습니다.

각 단계의 책임을 분리합니다.

```text
Requirement Extraction
Gap Analysis
Question Generation
Requirement Update
PRD Generation
PRD Validation
```

현재는 동일한 LLM을 여러 단계에서 사용해도 됩니다.

중요한 것은 **Agent 개수보다 Stage별 책임과 Contract를 구분하는 것**입니다.

가능하면 자유 형식 Text보다 Structured Output을 사용합니다.

후속 단계가 이전 단계의 결과를 다시 자연어로 추측해서 해석해야 하는 구조는 피합니다.

---

## 26. Prompt Management

Prompt는 가능하면 명시적으로 관리합니다.

예를 들어 다음과 같이 분리할 수 있습니다.

```text
prompts/
  requirement_extraction.md
  gap_analysis.md
  question_generation.md
  requirement_update.md
  prd_generation.md
  prd_validation.md
```

이 디렉터리 구조는 **권장안이며 확정된 제품 요구사항은 아닙니다.**

Repository의 기존 구조가 있다면 기존 Convention을 우선합니다.

---

# Part 3. Suggested Initial Implementation

이 Part는 현재 기획을 빠르게 구현하기 위한 **초기 제안**입니다.

팀 합의에 따라 변경할 수 있습니다.

Part 1의 Product Decision과 동일한 수준의 확정 사항으로 취급하지 않습니다.

---

## 27. Suggested Implementation Order

초기 구현은 다음 순서를 권장합니다.

```text
1. Core Schema / Contract 정의
2. Mock 기반 Backend 흐름 구현
3. 최소 Frontend 연결
4. End-to-End Vertical Slice 검증
5. 실제 LLM 연결
6. Prompt 및 Structured Output 안정화
7. Clarification Loop 구현
8. Requirement Review 구현
9. PRD Generation 구현
10. PRD Validation 구현
11. Error Handling
12. Test 보강
```

처음부터 전체 Workflow를 동시에 구현하기보다 작은 Vertical Slice를 먼저 실행 가능하게 만드는 것을 권장합니다.

---

## 28. Suggested Initial Vertical Slice for Current Requirement Extraction Stage

첫 Vertical Slice는 다음 범위로 제한할 수 있습니다.

```text
User Requirement Input
        ↓
Requirement Extraction
        ↓
Gap Analysis
        ↓
Requirements + Gaps 반환
        ↓
UI에 결과 표시
```

이 단계에서는 Clarification 이후 Workflow까지 한 번에 구현할 필요가 없습니다.

단, 반환 Schema는 이후 Clarification Module이 이어받을 수 있도록 구성합니다.

---

## 29. Suggested API

초기 구현에서는 다음과 같은 API를 사용할 수 있습니다.

```text
POST /api/requirements/analyze
```

예시 Request:

```json
{
  "text": "직원들이 휴가를 신청하고 관리자가 승인할 수 있는 서비스를 만들어줘."
}
```

예시 Response:

```json
{
  "project_id": "project-001",
  "run_id": "run-001",
  "clarification_round": 0,
  "requirements": [],
  "gaps": [],
  "clarification_needed": true
}
```

### Important

위 Endpoint 이름과 Response Envelope는 **초기 구현 제안**입니다.

현재 제품 기획에서 확정된 API Contract가 아닙니다.

팀이 더 적합한 Contract를 합의하면 변경할 수 있습니다.

---

## 30. Suggested Repository Structure

새 Repository를 구성하는 경우 다음 구조를 참고할 수 있습니다.

```text
.
├── AGENTS.md
├── README.md
├── .env.example
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── prompts/
│   └── tests/
│
├── frontend/
│   └── src/
│       ├── api/
│       ├── components/
│       └── pages/
│
└── docs/
    ├── prd/
    ├── specs/
    └── decisions/
```

이 구조 역시 권장안입니다.

이미 Repository 구조가 존재한다면 기존 구조를 우선합니다.

---

## 31. Environment Variables

LLM Provider 관련 값은 코드에 직접 작성하지 않습니다.

예:

```text
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
```

실제 환경 변수 이름은 현재 사용하는 Gateway 또는 Provider에 맞게 결정합니다.

`.env.example`에는 변수 이름과 필요한 설명만 포함하고 Secret 값은 포함하지 않습니다.

---

## 32. Testing Guidance

가능하면 다음 Case를 검증합니다.

```text
정상적인 자연어 Requirement

정보가 매우 부족한 Requirement

모호한 Requirement

빈 입력

Blocking Gap이 존재하는 경우

Blocking Gap이 존재하지 않는 경우

LLM이 예상 Schema와 다른 형식으로 응답한 경우

필수 필드가 누락된 경우
```

LLM 호출이 필요한 Test는 가능한 경우 Mock을 사용해 재현 가능하게 만듭니다.

---

## 33. Collaboration and Handoff

아래 담당 구분은 기존 M1의 인계 기록입니다. M2 팀 담당자는 아직 확정되지 않았으며 이 기록을 근거로 배정하지 않습니다.

M1은 여러 사람이 단계별로 이어서 구현할 수 있으므로 Module 간 Contract를 명확하게 유지합니다.

현재 구현 흐름은 대략 다음 책임으로 나뉩니다.

```text
권지연
User Input UI
→ Requirement Extraction
→ Requirement Structuring
→ 가능하면 Gap Analysis

        ↓ Handoff

한민희
직전 단계 보완
→ Clarification Loop
→ 가능하면 Requirement Review / Update

        ↓ Handoff

남윤아
Requirement Review / Update 이후
→ PRD Generation
→ PRD Validation
→ PRD Output
→ Final UI Validation
```

각 Coding Agent는 자신의 현재 Owner Scope를 넘어 후속 담당자의 기능을 선행 구현하지 않아야 하며, 앞 단계 담당자는 다음 단계 담당자가 내부 구현을 다시 분석하지 않아도 사용할 수 있는 형태로 결과를 전달해야 합니다.

Handoff 전 최소한 다음을 확인합니다.

```text
입력 형식이 명확한가

출력 형식이 명확한가

Mock인지 실제 LLM 호출인지 명확한가

환경 변수 요구사항이 문서화되어 있는가

실행 방법이 기록되어 있는가

다음 담당자가 바로 실행할 수 있는가
```

---

## 34. Coding Agent Completion Report

작업이 끝난 뒤 Coding Agent는 최소한 다음 내용을 정리합니다.

```text
변경한 파일

구현한 기능

사용하거나 변경한 Contract

주요 설계 결정

실행 또는 Test 결과

현재 미구현 범위

후속 작업자가 알아야 할 내용
```

설명만 작성하고 구현을 끝내지 않는 방식은 피합니다.

실제 동작 가능한 결과와 검증을 우선합니다.

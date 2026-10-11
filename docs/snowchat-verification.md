# SnowChat 연결 검증 — 2026-09-24

> 기존 M1의 과거 실행 기록입니다. 아래 수치와 결과는 해당 실행 당시의 증거이며 현재 브랜치의 재검증 결과가 아닙니다. M2 최종 제품은 Claude API를 사용하며 개발 기준은 [M2 결정사항](decisions/m2-development-direction.md)을 따릅니다.

## 실제 실행 결과

- 현재 Key의 모델 목록 조회 성공. `gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol` 모두 존재.
- 실제 사용 모델은 두 단계 모두 **gpt-5.6-luna**. Reasoning은 모델 기본값.
- 짧은 Chat Completion: `OK`, 입력 10 / 출력 4 tokens.
- 직접 Pipeline Smoke: 추출 5개 / Gap 7개. 두 단계 strict Structured Output과 Pydantic 검증 통과.
- 브라우저 → Vite → FastAPI → SnowChat → 화면 경로: HTTP 200, 추출 5개 / Gap 5개. SnowChat 모드 표시와 전체 10개 결과 행의 렌더링을 Playwright로 확인. 페이지 JavaScript 오류 없음.
- 기존 로컬 서버와 충돌을 피하려고 검증용 포트 8001/5175를 사용. 기본 실행 포트는 8000/5173.
- 외부 API 없는 Unit Test 14개 통과. TypeScript 검사와 Vite 빌드 통과.
- `.env` Git 제외 및 제출 가능한 파일에 실제 Key 문자열이 없음을 확인.

## 브라우저 실행의 실제 Usage

| Stage | 모델 | Input tokens | Output tokens | Stage latency (초) |
| --- | --- | ---: | ---: | ---: |
| Requirement Extraction | gpt-5.6-luna | 572 | 407 | 3.767 |
| Gap Analysis | gpt-5.6-luna | 921 | 553 | 9.067 |

합계 입력 1,493 / 출력 960 / 총 2,453 tokens. Gateway가 반환한 Usage를 그대로 보존했으며 비용은 추정하지 않았다. 첫 직접 Pipeline 실행은 입력 1,510 / 출력 1,002 tokens였으며 일반 Chat까지 포함한 이번 검증 호출의 합계는 4,979 tokens이다.

## Handoff

[브라우저 실행에서 받은 실제 JSON](examples/snowchat-handoff.json)에 전체 Requirements, Gaps, 관계 ID와 Usage가 있다. 예시에는 직원·관리자 역할, 신청·승인 기능, 사내 서비스 범위가 confirmed로 추출됐다. 누락된 상태·승인 범위·수정/취소·예외 정책은 Gap에만 포함됐다.

LLM 출력은 실행마다 달라진다. 이번 검증은 연결·계약·대표 시나리오 검증이며, 일반 서비스 전체에 대한 품질 평가가 아니다. Clarification 이후 단계는 구현하지 않았다. Mock은 명시적인 로컬 개발 및 Unit Test 모드로 유지한다.

## 구현 파일

- `backend/app/pipeline.py`: Provider 선택, 두 Stage 실행, ID/관계 검증, Usage 로그.
- `backend/app/llm/mock.py`: 기존 규칙 기반 Mock.
- `backend/app/llm/snowchat.py`: 환경 변수, 모델 조회/선택, Gateway 통신.
- `backend/app/llm/errors.py`: 사용자에게 전달 가능한 오류.
- `backend/app/schemas.py`: 기존 Handoff, 별도 ExtractionOutput/GapOutput, strict Schema.
- `backend/app/prompts/requirement_extraction.md`, `gap_analysis.md`: Stage별 Prompt.
- `backend/app/main.py`, `frontend/src/App.tsx`: 실제 Provider 연결, 모드 표시, 오류 전달.
- `backend/app/smoke.py`: 명시적으로 실행하는 실제 API 검증.
- `backend/tests/test_analysis.py`, `test_snowchat.py`: Mock 회귀 및 Gateway/Schema 실패 검증.
- `.env.example`, `README.md`: 설정·실행·모델 정책·인계 계약.

기존 `backend/app/analysis.py`의 규칙은 `llm/mock.py`로 이동했다. HTTP 요청/응답의 기존 필드는 유지하고 `analysis_mode`에 `snowchat`을 추가했으며 `usage` 단계 기록을 추가했다. 다음 담당자는 기존 `requirements`와 `gaps[].related_requirement_ids`를 그대로 사용할 수 있다.

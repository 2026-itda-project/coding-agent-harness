# Jev v2 결과: 지시·기준 영어 번역

같은 16개 dev 사례에서 16/16이 사전 정답과 일치했다. v1의 오답 4개가 모두 개선됐고 퇴행은 없었다. 이 표본에서 Sonnet v0의 15/16 목표를 넘었지만 모델 전반의 우월성·일반화·반복 안정성은 확인하지 않았다.

## 고정 조건과 변경

- v1의 instructions와 criteria만 영어로 번역. 원문과 판단 대상은 한국어 그대로 유지했다.
- jev-1.13.0, 같은 16개·정답·순서, 4개 라벨, 반복 1회. 예시 추가·질문 단계 분리 없음.
- Sonnet은 호출하지 않았다. 이전 한국어 프롬프트의 Sonnet v0는 참고 기준선이며 새 프롬프트 동시 비교군이 아니다.
- 번역의 표현 차이와 실행 변동도 있어 이번 결과만으로 영어 사용이 유일한 원인이라고 단정하지 않는다.

| 지표 | Jev v0 | Jev v1 | Jev v2 | Sonnet v0 (기존) |
| --- | --- | --- | --- | --- |
| 정답 일치 | 7/16 | 12/16 | 16/16 | 15/16 |
| 필수 질문 회수 | 3/4 | 4/4 | 4/4 | 4/4 |
| 과잉 질문 | 0/12 | 1/12 | 0/12 | 0/12 |
| 지연 중앙값 | 216ms | 217ms | 236ms | 3,239ms |
| 비용 USD | 0.000986202 | 0.000697242 | 0.000566874 | 0.101376 |
| confidence 0.6 미만 | 13/16 | 9/16 | 6/16 | 해당 없음 |

## 사례별 결과

| ID | 정답 | Jev v0 | Jev v1 | Jev v2 | v2 confidence |
| --- | --- | --- | --- | --- | --- |
| CORE-02 | ask_user | ask_user | ask_user | ask_user | 0.55 |
| CORE-06 | ask_user | ask_user | ask_user | ask_user | 0.69 |
| CONFLICT-01 | ask_user | ask_user | ask_user | ask_user | 0.59 |
| SCOPE-03 | ask_user | ai_refine | ask_user | ask_user | 0.83 |
| EXPLICIT-01 | ready | ai_refine | ready | ready | 0.97 |
| EXPLICIT-06 | ready | ai_refine | ready | ready | 0.67 |
| CONTEXT-01 | ready | ai_refine | ready | ready | 0.97 |
| CONTEXT-04 | ready | ai_refine | ready | ready | 0.89 |
| DEFAULT-01 | ai_refine | ai_refine | ai_refine | ai_refine | 0.69 |
| DEFAULT-03 | ai_refine | ai_refine | ask_user | ai_refine | 0.77 |
| DEFAULT-05 | ai_refine | ai_refine | ai_refine | ai_refine | 0.89 |
| DEFAULT-07 | ai_refine | ai_refine | ai_refine | ai_refine | 0.35 |
| SPEC-01 | defer_to_spec | ai_refine | ai_refine | defer_to_spec | 0.47 |
| SPEC-02 | defer_to_spec | ai_refine | ai_refine | defer_to_spec | 0.36 |
| SPEC-03 | defer_to_spec | ai_refine | ai_refine | defer_to_spec | 0.32 |
| SPEC-04 | defer_to_spec | ai_refine | defer_to_spec | defer_to_spec | 0.76 |

## 비용과 해석

- 실제 Jev 16회, 입력 13,497토큰·출력 840토큰. 오류·자동 retry·fallback 없음. Sonnet 추가 호출 0회.
- 응답 usage와 입력 100만 토큰당 USD 0.042로 계산한 비용은 USD 0.000566874. 사전 약 USD 0.001 예상, 보수적 허용량 합계 USD 0.0046, 예산 검사 USD 0.01 이내였다. 청구서 조회 금액은 아니다.
- 연결 확인+v0+v1+v2의 기록상 누적 계산 비용은 USD 0.137147078이다.
- 해결된 v1 오답: DEFAULT-03 빈 목록 안내, SPEC-01 버튼 배치, SPEC-02 API 경로/필드, SPEC-03 DB 구조.
- confidence < 0.6은 6/16개다. 정답 일치와 confidence 보정은 별개이며 실제 혼합 흐름에서는 재검토 비용을 따로 확인해야 한다.
- 현재는 v2를 다음 검증 후보로 고정한다. v3를 바로 추가하거나 같은 dev 정답에 맞춰 문구를 더 바꾸지 않는다. 반복 안정성과 별도 사례 검증이 후속 과제다.
- v0·v1 파일과 평가셋을 변경하지 않았다. 실제 로그는 results/compare-v2-live-01/calls.jsonl에 Git 제외로 보관한다.

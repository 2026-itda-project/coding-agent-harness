# 16개 소규모 판정 비교 결과

결론: 현재 프롬프트·정책·한국어 입력 조건에서는 Jev 4분류 도입을 보류한다. 비용·지연은 낮았지만 판정 품질 기준을 만족하지 못했다. Jev 모델 전체의 성능이나 M1 대비 사용자 경험을 일반화한 결과가 아니다.

## 실행 조건

- dev 16개, 라벨별 4개, 모델별 1회. 총 32회 정상 응답, 자동 retry·fallback·추출·내용 생성 없음.
- Jev: jev-1.13.0. Sonnet: claude-sonnet-5-5, 출력 상한 1,024토큰, thinking은 API 기본값. 동일 원문·대상·정책·기준을 각 API 형식에 맞춰 전달.
- 판정 모델의 설명문은 요청하지 않았다. 아래 해석은 관측된 라벨에 대한 분석이며 모델이 말한 원인 설명이 아니다.
- 최초 실제 사용한 입력·정책·코드 해시는 results/compare-live-01/calls.jsonl에 보존했다. 원본 로그는 Git 제외.

| 지표 | Jev | Sonnet |
| --- | --- | --- |
| 정답 일치 | 7/16 (43.75%) | 15/16 (93.75%) |
| 필수 질문 회수 | 3/4 | 4/4 |
| 불필요한 질문 | 0/12 | 0/12 |
| 지연 중앙값 | 216ms | 3,239ms |
| 입력 / 출력 토큰 | 23,481 / 845 | 32,403 / 3,657 |
| 표준 단가 계산 비용 | $0.000986202 | $0.101376 |

합계 $0.102362202. 이번 32회만의 비용이며 이전 연결 확인 비용은 제외한다. 계정 청구서를 조회한 금액이 아닌 응답 usage × 기록 단가다. 예산 $0.50 이내이며 자동 재실행하지 않았다.

## 오판과 도입 판단

- Jev: ready 정답 4개와 defer_to_spec 정답 4개를 모두 ai_refine으로 분류. 실제 결제 요구 SCOPE-03도 ai_refine으로 보내 사용자 확인을 놓쳤다. ai_refine으로 13/16개가 몰렸다.
- Sonnet: DEFAULT-07 초기 시연 데이터 보완을 ready로 분류한 1개 불일치. 이미 정책에 초기 데이터 지원이 있으므로 ready인지, 구체적인 시연 내용을 아직 써야 해서 ai_refine인지 라벨 경계 재검토가 필요하다. 이번 정답·점수는 사후 변경하지 않았다.
- Jev confidence < 0.6은 13/16개(81.25%). 기존 fallback 조건을 적용하면 이 비율이 Sonnet 재검토 대상이 될 것으로 예상된다. 실제 혼합 흐름을 실행한 측정값은 아니다.
- confidence가 0.6 이상인 3개 중 SPEC-02와 SPEC-03 두 개는 오판이었다. 따라서 현재 임계값만으로 오판을 걸러낸다고 주장할 수 없다.
- 두 모델 모두 과잉 질문이 0건이므로 이번 표본에서는 Jev의 질문 감소 이점이 관측되지 않았다. 단독 판정 비용은 약 99% 낮고 중앙 지연은 약 93% 짧지만, 품질 조건을 통과하지 못해 그것만으로 도입하지 않는다.

## 사례별 결과

| ID | 고정 정답 | Jev | confidence | Sonnet |
| --- | --- | --- | --- | --- |
| CORE-02 | ask_user | ask_user | 0.35 | ask_user |
| CORE-06 | ask_user | ask_user | 0.45 | ask_user |
| CONFLICT-01 | ask_user | ask_user | 0.23 | ask_user |
| SCOPE-03 | ask_user | ai_refine | 0.35 | ask_user |
| EXPLICIT-01 | ready | ai_refine | 0.25 | ready |
| EXPLICIT-06 | ready | ai_refine | 0.55 | ready |
| CONTEXT-01 | ready | ai_refine | 0.39 | ready |
| CONTEXT-04 | ready | ai_refine | 0.51 | ready |
| DEFAULT-01 | ai_refine | ai_refine | 0.6 | ai_refine |
| DEFAULT-03 | ai_refine | ai_refine | 0.28 | ai_refine |
| DEFAULT-05 | ai_refine | ai_refine | 0.55 | ai_refine |
| DEFAULT-07 | ai_refine | ai_refine | 0.59 | ready |
| SPEC-01 | defer_to_spec | ai_refine | 0.44 | defer_to_spec |
| SPEC-02 | defer_to_spec | ai_refine | 0.67 | defer_to_spec |
| SPEC-03 | defer_to_spec | ai_refine | 0.65 | defer_to_spec |
| SPEC-04 | defer_to_spec | ai_refine | 0.36 | defer_to_spec |

## 다음 검토 — 추가 호출 전

- 평가셋·현재 결과는 보존. 정답 경계의 모호함을 검토하되 이전 점수를 유리하게 다시 쓰지 않는다.
- Jev가 ai_refine에 몰리는 이유를 정책·criteria 전달 방식, 한국어 이해, 제품 보완과 구현 메모 경계로 나눠 조사한다. 원인이 확인되기 전에는 단정하지 않는다.
- 필요하면 dev에서 질문 필요 여부만 판정하는 더 좁은 역할 또는 기준 문구 수정안을 비교한다. 새 문제/프롬프트의 결과는 현재 4분류 결과와 별도 실험으로 기록한다.
- 지금은 test 70개나 반복 3회로 확대하지 않는다. 다음 실험의 예상 비용을 먼저 안내한다.
- M1/M2 정책 효과 및 실제 사용자 경험 비교는 아직 실행하지 않았다. 이 결과는 합성 소규모 dev 진단이며 독립 일반화 검증이 아니다.

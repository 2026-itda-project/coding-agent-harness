# Jev v1 결과: 판정 프롬프트 정리

같은 16개 dev 사례에서 v0 7/16 → v1 12/16으로 개선했다. 필수 질문 4개는 모두 회수했지만 과잉 질문 1개와 Spec 구분 오류 3개가 남아 있다. Sonnet v0 15/16 수준에는 아직 못 미친다. 제품 도입이나 새로운 입력에서의 일반화는 검증하지 않았다.

## 변경과 통제

- 한국어 instructions/criteria를 정리하고 질문 라운드·confidence 후처리 규칙을 판정 지시에서 제거했다. 판단 대상에 집중하고 명시 충돌·충분함·구현 세부·제품 보완의 경계를 명시했다.
- 모델 jev-1.13.0, state, 4개 라벨, 평가셋·정답·순서와 1회 실행은 동일하다. few-shot 예시를 추가하지 않았다.
- Sonnet은 다시 호출하지 않았다. 저장된 Sonnet v0는 이전 프롬프트의 기준선이며 동일한 새 프롬프트로 재대결한 결과가 아니다.
- 여러 문구를 함께 바꿨으므로 개별 문구의 인과 효과를 분리한 실험은 아니다.

| 지표 | Jev v0 | Jev v1 | Sonnet v0 (기존) |
| --- | --- | --- | --- |
| 정답 일치 | 7/16 | 12/16 | 15/16 |
| 필수 질문 회수 | 3/4 | 4/4 | 4/4 |
| 과잉 질문 | 0/12 | 1/12 | 0/12 |
| 지연 중앙값 | 216ms | 217ms | 3,239ms |
| 판정 비용 USD | 0.000986202 | 0.000697242 | 0.101376 |
| confidence 0.6 미만 | 13/16 | 9/16 | 비교 안 함 |

이번에는 Jev 16회만 실행했다. 입력 16,601토큰·출력 836토큰, usage 기준 계산 비용 USD 0.000697242. 실패·재시도·fallback 없음. Sonnet 추가 비용 없음. 기존 연결 확인+v0 비교를 포함한 기록상 누적 비용은 USD 0.136580204이며 실제 청구서 확인 금액은 아니다.

## 사례별 변화

| ID | 정답 | Jev v0 | Jev v1 | v1 confidence | Sonnet v0 |
| --- | --- | --- | --- | --- | --- |
| CORE-02 | ask_user | ask_user | ask_user | 0.81 | ask_user |
| CORE-06 | ask_user | ask_user | ask_user | 0.91 | ask_user |
| CONFLICT-01 | ask_user | ask_user | ask_user | 0.67 | ask_user |
| SCOPE-03 | ask_user | ai_refine | ask_user | 0.91 | ask_user |
| EXPLICIT-01 | ready | ai_refine | ready | 0.97 | ready |
| EXPLICIT-06 | ready | ai_refine | ready | 0.53 | ready |
| CONTEXT-01 | ready | ai_refine | ready | 0.93 | ready |
| CONTEXT-04 | ready | ai_refine | ready | 0.84 | ready |
| DEFAULT-01 | ai_refine | ai_refine | ai_refine | 0.52 | ai_refine |
| DEFAULT-03 | ai_refine | ai_refine | ask_user | 0.31 | ai_refine |
| DEFAULT-05 | ai_refine | ai_refine | ai_refine | 0.31 | ai_refine |
| DEFAULT-07 | ai_refine | ai_refine | ai_refine | 0.21 | ready |
| SPEC-01 | defer_to_spec | ai_refine | ai_refine | 0.16 | defer_to_spec |
| SPEC-02 | defer_to_spec | ai_refine | ai_refine | 0.53 | defer_to_spec |
| SPEC-03 | defer_to_spec | ai_refine | ai_refine | 0.09 | defer_to_spec |
| SPEC-04 | defer_to_spec | ai_refine | defer_to_spec | 0.35 | defer_to_spec |

## 해석과 다음 후보

- 개선 6개: SCOPE-03 실제 결제 확인, EXPLICIT-01/06 및 CONTEXT-01/04의 ready 판정, SPEC-04의 Spec 이관.
- 퇴행 1개: DEFAULT-03 대출 기록이 없을 때의 목록 안내를 ai_refine 대신 ask_user로 판정. 이유 문장은 요청하지 않아 원인은 미확정.
- 잔여 Spec 오판: SPEC-01 버튼 배치, SPEC-02 API 경로·응답 필드명, SPEC-03 SQLite 테이블 구조를 ai_refine으로 분류.
- v1 프롬프트는 여기서 고정한다. 다음에는 지시 언어만 바꾸는 v2 또는 제품 보완/구현 세부를 별도 판단으로 나누는 안을 검토한다. 아직 실행하지 않았다.
- 개발용 사례를 보고 만든 프롬프트의 성적이므로 과적합 가능성이 있다. 정답을 사후 수정하지 않으며, 확대 검증 전에 입력·정답·비용을 다시 고정한다.
- 원본 로그: experiments/jev/results/compare-v1-live-01/calls.jsonl (Git 제외). 버전 폴더에는 정제된 결과만 보관한다.

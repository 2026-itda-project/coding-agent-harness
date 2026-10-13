# Jev 요구사항 판정 실험

[버전 보관 구조](versions/README.md) · [v0 결과](versions/v0/report.md) · [고정 소규모 평가셋](datasets/pilot-v1.json)

제품 코드와 분리한 실험이다. Python 표준 라이브러리만 사용한다. 공식 제품 정책·Jev 도입 여부는 바꾸지 않는다.

## 현재 상태: v1 비교 완료

[첫 16개 실제 비교 결과](evaluation/pilot-01-results.md): Jev 7/16, Sonnet 15/16. 현재 조건에서 도입 보류. 추가 호출 전 비용 안내.

[비교 실험 계획과 대표 검수 사례](evaluation/plan.md) · [140개 평가셋 수정안](evaluation/cases-review.md)

대표 판정 경계에 대한 사용자 동의 후, 수정안에서 dev 16개를 선정해 Jev·Sonnet 단독으로 각각 1회씩 실제 비교했다. 총 32회 호출의 계산 비용은 USD 0.102362202였다. 전체 140개의 독립 정답 검수와 별도 평가셋 검증은 아직 남아 있다. 추가 유료 실행 전 예상 비용을 먼저 안내한다.

현재 결과는 [versions/v0](versions/v0/)에 보존한다. v1은 판정 프롬프트를 정리해 Jev만 같은 16개를 추가 실행했다. **12/16**, 필수 질문 4/4, 과잉 질문 1/12, 비용 USD 0.000697242였다. [v1 결과](versions/v1/report.md)를 참고한다. Sonnet은 재호출하지 않았다. `compare.py`는 v0에서 고정한 `datasets/pilot-v1.json`과 선택 버전의 프롬프트를 읽고 해시를 검사한다. 기존 `run.py`는 원본 120개를 사용하므로 평가셋을 혼동하지 않는다.

## 고정 조건: demo-gate-v1

- 목표: 완벽한 서비스 명세보다 동작하는 데모를 만들 수 있는 요구사항 정리.
- 질문 최대 **3라운드 × 라운드당 3개**. 세 번째 답변을 반영한 뒤에도 남은 미결정은 Sonnet이 데모용 가정으로 작성한다.
- 보완 내용은 `proposed / ai_proposal`. 사용자 확정으로 자동 변경하지 않고 Review에 표시한다. 원문과 답변은 그대로 보존한다.
- 사소한 미결정은 사용자 질문 없이 AI 보완. 구현 세부는 Spec 이관.
- Jev confidence **0.6 미만**은 Sonnet 재검토. 0.6은 미보정 초기값이며 정확도나 도입 기준이 아니다. dev 평가 후 바꾸면 새 실험 버전으로 시작한다.
- Jev 모델 `jev-1.13.0`. Sonnet은 실제 계정에서 사용할 고정 모델 ID를 `SONNET_MODEL`로 지정한다. latest는 쓰지 않는다.

정책 입력: [policy.md](policy.md). 조건 상수: [pipeline.py](pipeline.py)의 `CONDITIONS`.

| 판정 | 처리 |
| --- | --- |
| ready | 사용자 원문과 상태 유지 |
| ai_refine | Sonnet이 AI 제안 작성 |
| ask_user | Sonnet이 쉬운 질문 작성. 상한 도달 후에는 AI 제안 작성 |
| defer_to_spec | Sonnet이 구현 메모 작성. 제품 결정을 숨기는 용도로 사용하지 않음 |

명시적인 실제 결제 요구를 모의 결제로 조용히 바꾸지 않는다. 범위 충돌은 질문하고, 라운드 종료 후에도 남으면 선택한 데모 가정과 원문과의 차이를 표시한다. 이 표시의 의미적 정확성은 실제 모델 응답을 검토해야 한다.

## 세 실행기의 역할

### compare.py: Jev·Sonnet 단독 비교

`compare.py` 기본값 `--version v0`는 고정 dev 16개를 두 모델에 1회씩 요청한다. `--version v1`은 같은 사례로 Jev만 호출하며 Sonnet v0는 저장된 참고 결과다. 동일 입력·기준, 생성·fallback·자동 retry 없음. 기본 dry-run이고 `--live`만 과금한다. Sonnet 출력 상한은 1,024토큰이다.

```bash
python3 experiments/jev/compare.py --out experiments/jev/results/compare-dry-new
# 실행 전 예상 비용을 먼저 안내한 뒤 사용
uv run --no-project --env-file .env python experiments/jev/compare.py --live --out experiments/jev/results/compare-live-new
```

$0.50 예산에 대해 누적 usage 기반 계산 비용과 다음 요청의 보수적 입력 byte 수+4,096토큰 여유분 및 최대 출력을 합쳐 실행 여부를 검사한다. 이는 공급자의 청구 상한 기능이 아니다. 사용량 누락·예상 밖 캐시 요금·예약 비용 초과·응답 오류 시 즉시 중단한다. 이미 보낸 요청의 청구는 취소하지 못한다. 원본 요청/응답과 예산·단가·파일 해시를 호출별로 저장한다.

### run.py: 순수 Jev 판정 비교

고정 합성 사례 [cases.json](cases.json), [읽기용 목록](cases.md)의 120개 발화를 판정한다. 6개 유형 × 20개이며 dev/test는 각각 60개로 도메인이 겹치지 않는다. 기대 라벨·이유는 검토용 초안이며 모델 입력에서 제외한다.

원본 120개는 ready/ai_refine/ask_user 기준으로 작성했으며 **이 원본 파일에는 defer_to_spec 정답이 없다**. 재검수한 140개 수정안과 v0 비교용 16개는 별도 파일로 보존한다. 이 실행기는 기존 원본 점검용이며, v0 단독 비교 재현에는 아래 `compare.py`와 버전 기록을 사용한다.

```bash
python3 experiments/jev/run.py
python3 experiments/jev/run.py --split test
python3 experiments/jev/run.py --category implicit_core_gap --limit 2
# 과금 가능한 실제 호출은 명시적으로 선택한다.
uv run --no-project --env-file .env python experiments/jev/run.py --live --split dev --limit 1
```

기본 실행은 호출 없는 dry-run이다. 실제 호출은 `TYPESAFE_API_KEY`가 필요하다. `--repeats 1..3`으로 반복할 수 있고, 순수 판정기는 오류 첫 발생 시 중단한다. 전체·유형별 혼동행렬과 질문 회수율·과잉 질문률, usage·지연을 기록한다. 비용과 실제 질문 라운드는 미측정 null이다. Sonnet 대체 결과를 Jev 정확도에 섞지 않는다.

### pipeline.py: 질문·답변을 포함한 실행 흐름

```text
사용자 원문 → Sonnet 요구사항·Gap 추출 (고정 fixture 입력이면 생략)
→ 모든 요구사항 + 연결되지 않은 Gap을 Jev에 한 번에 판정 요청
→ 낮은 confidence 또는 Jev 실패 시 Sonnet 재검토
→ Sonnet 보완·질문·Spec 메모 작성
→ 질문이 있으면 세션 저장 후 실제 사용자 답변 대기
→ 답변을 누적해 같은 후보를 재판정 (최대 3라운드)
→ Review용 결과 저장
```

- 연결된 Gap은 첫 번째 관련 요구사항에 배정해 중복 질문을 줄인다. 전체 요구사항·Gap·원문·이전 질문/답변은 판단 Context로 전달한다.
- 상한 초과 질문은 대기한다. 덜 물어본 항목부터, 동률이면 fixture 순서로 선택한다. 영향도 score나 의미 기반 질문 병합은 구현하지 않았다.
- 한 ID에 대한 AI 보완은 갱신해 중복 누적을 막는다. 이전 판정과 제안은 실행별 세션 파일로 남는다.
- 대상 ID 누락·추가, 참조 오류, 잘못된 판정·확률·confidence·모델 버전, 빈 생성 문장은 거부한다.
- Jev 429/529만 최초 호출 포함 최대 3회(1초/2초 대기, 각 요청 timeout 30초). 실패하면 Sonnet 재검토로 넘기고 판정자를 기록한다. Sonnet 실패·잘못된 산출물은 실행 실패이며 임의 제안으로 위장하지 않는다. Sonnet은 60초 timeout, 자동 재시도 없음.
- 토큰과 지연은 `calls.jsonl`의 **호출 단위**로 남긴다. 항목별 판정 기록에 usage를 중복 합산하지 않는다. fallback·재시도 비용까지 포함하는 전체 흐름 평가는 순수 Jev 평가와 구분한다.

저장소 루트에서, API 키 없이 요청 구성을 확인한다. `--out`은 새 폴더여야 한다.

```bash
python3 experiments/jev/pipeline.py \
  --input experiments/jev/pipeline-input.json \
  --out experiments/jev/results/gate-dry-01
```

[pipeline-input.json](pipeline-input.json)은 정답 라벨 없는 고정 후보 예시다. Sonnet 추출부터 시작하려면 입력 JSON을 `{"user_input": "만들고 싶은 데모 설명"}` 형태로 준비한다.

실제 실행에는 환경 변수 `TYPESAFE_API_KEY`, `ANTHROPIC_API_KEY`, `SONNET_MODEL`이 필요하다. 저장소 루트의 `.env.example`을 `.env`로 복사하고 두 API 키를 채운다. 초기 모델은 `claude-sonnet-5-5`로 지정했다. 기존 M1용 `FACTCHAT_API_KEY` 등은 이 실험에 필요 없다. `.env`는 Git 제외이며 키를 로그·프롬프트에 넣지 않는다. 스크립트 자체는 `.env`를 자동으로 읽지 않으므로 아래처럼 uv로 로드한다. 아래 명령의 `--live`는 과금 가능한 호출을 활성화한다.

```bash
uv run --no-project --env-file .env python experiments/jev/pipeline.py --live \
  --input experiments/jev/pipeline-input.json \
  --out experiments/jev/results/gate-live-01
```

`session.json`의 `session.pending_questions`에 질문 ID와 문장이 저장된다. 표시된 ID마다 실제 답변을 작성한다. 예: `{"REQ-001": "신청 순서대로 승인해줘"}`. ID는 실제 결과와 일치해야 하며, 빈 답변은 허용하지 않는다. 모르면 `"모르겠어요"`라고 답할 수 있다.

```bash
uv run --no-project --env-file .env python experiments/jev/pipeline.py --live \
  --resume experiments/jev/results/gate-live-01/session.json \
  --answers experiments/jev/results/answers-01.json \
  --out experiments/jev/results/gate-live-02
```

질문이 남으면 새 세션과 새 답변 파일로 반복한다. `phase=review`면 종료다. `document`와 `previous_rounds`는 사용자 근거, `proposals`는 AI 가정, `spec_notes`는 Spec 이관 목록이다. 실제 Review 승인·PRD 생성은 후속 제품 단계다.

정책·코드·모델·조건이 바뀐 세션은 이어 실행하지 않는다. 출력은 덮어쓰지 않고, 호출마다 로그를 즉시 저장한다. 실패 시 성공 세션을 쓰지 않는다. 같은 입력으로 새 폴더에 재실행할 수 있지만 이전 호출 비용은 이미 발생했을 수 있다. 로그에는 원문과 응답이 포함되므로 기본 `results/`는 Git 제외다.

## 검증과 실험 한계

```bash
python3 -m unittest discover -s experiments/jev -p 'test_*.py' -v
```

오프라인 테스트는 분기·라운드 제한·참조 검증·실패·재개를 확인한다. 모델 의미 판단의 정확도를 증명하지 않는다. dry-run은 다음 요청만 구성하며 실제 모델 판단을 만들어내지 않는다.

현재 파이프라인은 최초 추출 후보를 고정해 답변 Context를 누적하는 실험이다. 답변에서 새 요구사항·Gap을 재추출하는 제품 기능은 포함하지 않는다. 같은 요구사항 안의 여러 독립 Gap을 각각 판정하는 세분화도 후속 검증 대상이다.

실제 API 연결 확인과 16개 Jev·Sonnet 단독 비교는 완료했다. 평가셋 140개 수정안도 준비했지만 전체 라벨의 독립 검수·별도 평가셋 검증·confidence 보정·최종 도입 기준 확정은 남아 있다. 현재 결과만으로 한국어 전반의 성능이나 M1 대비 사용자 경험 개선을 주장하지 않는다. PRD·Spec·개발 실행은 이 실험 범위 밖이다.

## API 근거

- [TypeSafe API](https://docs.typesafe.ai/api), [모델](https://docs.typesafe.ai/models), [confidence](https://docs.typesafe.ai/confidence)
- [Claude Messages API](https://platform.claude.com/docs/en/api/messages/create), [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

Jev의 state/instructions/criteria를 조정하는 실험이며 고객 데이터로 모델을 fine-tuning하는 작업이 아니다. 합성 dev/test 분할은 독립 검수된 고객 분포를 뜻하지 않는다.


### Jev v1만 실행

```bash
python3 experiments/jev/compare.py --version v1 --out experiments/jev/results/v1-dry-new
# 사전 비용 안내 후 실행. Sonnet 호출 없음.
uv run --no-project --env-file .env python experiments/jev/compare.py --version v1 --live --out experiments/jev/results/v1-live-new
```

v1은 TYPESAFE_API_KEY만 필요하다. 누적 비용과 다음 요청 예상치를 검사하는 예산은 USD 0.01이며, 자동 재시도나 fallback은 없다. v0 모델·프롬프트·고정 평가셋 파일은 변경하지 않는다.

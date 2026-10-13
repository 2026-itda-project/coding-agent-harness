# 실험 버전 보관

`v0/`는 실제 실행 `compare-live-01`에서 추출한 Jev·Sonnet 공통 기준선이다. 이후 프롬프트를 수정해도 이 폴더와 `datasets/pilot-v1.json`은 덮어쓰지 않는다.

- `config.json`: 모델·호출 설정·예산·평가셋 및 프롬프트 해시·실제 실행 당시 코드 해시.
- `prompt.json`: 실제 요청에서 추출한 Jev instructions/criteria와 Sonnet system/출력 스키마. Sonnet user 메시지는 같은 state와 criteria를 JSON으로 직렬화한 것이다.
- `predictions.json`: 사례별 정답·판정·confidence·usage·지연·계산 비용. 원문 응답·thinking·인증 정보 제외.
- `summary.json`: 실제 집계 결과.
- `report.md`: 결과 해석과 한계.
- `../../datasets/pilot-v1.json`: 실제 사용한 순서대로 고정한 16개 합성 평가 사례. 정답·이유는 평가에만 사용하고 모델에게 보내지 않는다.

공통 실행 코드와 테스트는 `experiments/jev/`에 둔다. 현재 실행기는 이 스냅샷을 선택하는 버전 인자를 지원하지 않으며 기존 evaluation manifest와 policy.md를 사용한다. v1 작업 시 별도 프롬프트 선택을 연결하고, v0 재실행은 이 기준선이 담긴 Git 커밋의 코드를 사용하거나 저장된 프롬프트·설정을 명시적으로 읽도록 한다. 다른 조건으로 재실행한 결과를 v0로 덮어쓰지 않는다.

원본 요청·응답은 `results/compare-live-01/`에 Git 제외 상태로 남긴다. Git만으로 원본 thinking이나 공급자 응답 전체를 복원할 수는 없다. 필요하면 접근 제한된 별도 저장소에 백업한다.

향후 커밋은 v0 기준선, v1 조건 변경, v1 결과 순서로 나눈다. 아직 실행하지 않은 버전의 점수나 결과 파일은 만들지 않는다.

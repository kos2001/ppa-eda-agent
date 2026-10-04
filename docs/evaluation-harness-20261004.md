# 평가 예산과 파레토 메모리

HW 탐색의 피드백 비용을 하네스에서 기록하고 제한한다. 기존 OpenLane
평가·최종 검증을 유지하면서 후보 생성, 스크리닝, 수리, polish에 같은 예산을
적용한다. 목적값이 없는 후보를 0으로 취급하지 않는다.

## 실행 계획

아래는 실행하지 않은 예시다. 실제 실행 전에 설계의 run spec으로 저장하고
`--validate-only`로 검사한다.

```json
{
  "design_name": "counter4",
  "candidate_budget": 2,
  "evaluation_budget": {
    "max_evaluations": 4,
    "max_wall_seconds": 900
  },
  "search": {
    "evaluation_order": "measured_cost",
    "objective": "signoff-clean Pareto frontier",
    "reference": "compatible reference-db measurements"
  },
  "targets": { "max_core_utilization": 0.75 },
  "candidates": [
    { "tag": "budget-util25", "overrides": { "FP_CORE_UTIL": 25 } },
    { "tag": "budget-util35", "overrides": { "FP_CORE_UTIL": 35 } }
  ]
}
```

```sh
python3 pipeline/orchestrator.py \
  --design pipeline/designs/counter4 \
  --run-spec /path/to/run_spec.json --validate-only
```

`candidate_budget`는 초기 후보 수를 제한한다. `evaluation_budget`는 전체
루프에서 새 평가를 시작할 수 있는 횟수와 시간을 제한한다. 두 필드는 다르다.

- 스크리닝 후보당 1회, 전체 OpenLane flow 후보당 1회다. 두 후보를 모두
  스크리닝한 뒤 모두 전체 평가하면 4회다. 수리·polish도 같은 예산을 쓴다.
- `SynthesisExploration` 호출은 1회로 계산한다. 그 호출 안에서 여러 합성
  전략을 평가하므로 개별 전략 수나 OpenLane 내부 단계 수의 제한은 아니다.
- 시간 제한은 orchestration 시작 이후의 실제 경과 시간으로 검사한다.
  제한에 도달하면 새 평가를 시작하지 않는다. 이미 시작한 평가와 검증은
  끝까지 수행하므로 전체 종료 시간은 제한을 넘을 수 있다.
- 병렬 worker는 시작 직전에 같은 예산을 예약한다. 대기 중인 후보도
  시작할 때 다시 검사한다. 한 worker의 완료를 다른 worker의 예산으로 쓰지 않는다.
- 제한 때문에 전체 평가를 못 한 후보는 `NOT EVALUATED`다. 도구 실패나
  검증 실패가 아니다. 스크리닝까지 실행했으면 그 결과와 시간을 보존한다.
- 예산 소진 전에 검증된 winner가 있으면 유지한다. 없으면
  `evaluation_budget_exhausted`로 끝난다. 예산을 명시하지 않은 기존 spec의
  실행 정책은 그대로다.

예산 소진은 원인 불명의 tool failure 리뷰 요청을 만들지 않는다.
Action Center는 기록된 한도 확인으로 연결하며, 반복 횟수만 늘리는 재실행
버튼을 제공하지 않는다. 평가 예산은 run spec에서 조정하고 다시 검증한다.

## 단계별 측정

새 전체 평가의 `seconds`는 입력 캡처와 준비부터 결과 판정, 요청된 등가성
검증, 출처 기록까지 포함한다. `stage_costs`에는 `preparation`, `openlane`,
`assessment_and_verification`, 실행된 경우 `provenance`를 기록한다.
OpenLane 실패도 소요 시간을 남기며, 실행하지 않은 후속 단계의 시간은
만들지 않는다. 스크리닝과 합성 탐색은 별도로 기록한다.

이 기록은 아직 lint → 시뮬레이션 → 합성 → P&R 각 단계의 별도 실행기를
새로 구현한 것이 아니다. 기존 early-utilization 스크리닝, 합성 탐색,
전체 flow의 비용을 분리해 다음 최적화에 필요한 피드백을 수집한다.
초기 단계의 좋은 수치는 최종 signoff나 모델 자격을 대신하지 않는다.

## 파레토 아카이브

```sh
# 원본 case는 수정하지 않으며 EDA 도구를 실행하지 않는다.
python3 pipeline/evaluation_archive.py
# 파생 캐시를 원자적으로 갱신한다.
python3 pipeline/evaluation_archive.py --write
```

원본은 `reference-db/cases/*.json`, 파생 캐시는
`reference-db/.cache/pareto-archive.json`이다. 새 case를 저장할 때 갱신하고,
`GET /evaluation-report`도 같은 보고서를 제공한다. 캐시는 재생성할 수 있다.

기록된 통과 판정뿐 아니라 최종 Magic DRC, KLayout DRC, LVS의 실제 0건
기록, 모든 보고된 corner의 setup/hold, 모델 자격, 요청한 비공허 RTL/netlist
등가성 검증을 검사한다. 누락값이나 불완전한 검증은 아카이브에서 제외한다.

호환성 키는 RTL·include·검증 소스와 설정의 해시, 실제 최종 SDC, resolved
Liberty와 macro 물리 view, 선택한 PDK 버전, SCL, 기록된 tool image/revision,
평가기 소스, flow 및 의미를 바꾸는 override를 포함한다. custom flow에는
실행 시 캡처한 소스 해시가 필요하다. 해석할 수 없는 입력 표현식, include,
외부 `$readmem` 데이터, 확인할 수 없는 PDK 버전은 출처 미확인으로 둔다.
과거 기록에 현재 소스의 출처를 소급해서 붙이지 않는다.

Image 필드는 기존 하네스가 기록하는 pinned image 참조이고 revision은
기대 OpenROAD revision이다. 실행 중인 image의 실제 digest를 새로 검증하는
기능은 포함하지 않는다. 모델 해시는 물리 측정에 의한 모델 자격을 뜻하지 않는다.

같은 키 안에서 공통으로 측정된 면적, 전력, core 면적, setup slack으로
비지배 front를 유지한다. 임의 가중합으로 파레토 아카이브를 대체하지 않는다.
값이 하나라도 없거나 비유한이면 그 목적 축을 비교 집합 전체에서 제외한다.
실제 측정된 0은 보존한다. 활동 기반 전력과 vectorless 전력을 섞어 비교하지 않는다.

## 측정 비용으로 실행 순서 조정

기본 `search.evaluation_order`는 `spec`이다. `measured_cost`는 같은 설계,
요청 기술, image, host, 입력 해시, flow, 등가성 요청 정책에 해당하는
완료된 전체 평가 시간이 3개 이상 있을 때 그 중앙값으로 순서를 조정한다.
실패 시간, 스크리닝 시간, 출처가 없는 과거 시간으로 전체 flow 비용을
예측하지 않는다. 예측값과 표본 수를 후보의 `scheduling`에 남긴다.

미측정 후보 하나를 먼저 두고, 비용을 아는 후보는 낮은 비용 순서로 둔다.
남은 미측정 후보는 선언 순서를 유지한다. 후보를 삭제하거나 PPA를 예측해
검증 없이 선택하지 않는다. 같은 cohort라도 파라미터에 따라 실제 시간이
다를 수 있다. 호환 표본이 없으면 선언 순서와 미확인 비용을 유지한다.

## 현재 기록과 다음 단계

2026-10-04 기준 기존 488개 후보 중 실행 시간은 228개에 있고 260개는
미확인이다. 단계별 시간 기록은 0개다. 엄격한 호환 아카이브는 0개이며,
물리 검사까지 확인된 23개 통과 기록에도 새로운 출처 스냅샷은 없다.
대시보드 Overview에 이 수치와 단계별 기록, 비교 그룹을 표시한다.

다음 유효 평가부터 이 기록을 축적한 뒤 승격률과 평가 비용을 측정하고,
비싼 단계에 예산을 배분하는 모델을 평가할 수 있다. 현재 구현은 GP나
MF-HVKG를 학습하지 않는다. 알고리즘 참고는
[BoTorch의 다목적·멀티 피델리티 예제](https://botorch.org/docs/next/tutorials/Multi_objective_multi_fidelity_BO)다.

RTL/netlist 등가성은 원래 RTL의 명세 정확성을 증명하지 않는다. 독립적인
golden reference·formal 명세 검증은 별도로 필요하다. corner STA나 일치하는
PVT 라벨을 공정 몬테카를로 수율로 해석하지 않는다. SRAM의 물리 검사와
모델 자격은 계속 별도 hard gate다.

## 검증 결과

- Python 전체 테스트: 1,181개, 통과, 환경 조건에 따른 기존 4개 건너뜀.
- 프론트엔드 production build 통과. Lint 오류 0개, 기존 Fast Refresh
  경고 7개 유지.
- Python과 TypeScript의 파레토 front·공통 목적 축을 실제 전체 case store와
  누락값·미확인 corner fixture에서 비교해 일치함을 확인했다.
- 병렬 예산 예약, deadline 후 검증 완료, 수리·polish·스크리닝·합성 탐색의
  공유 예산, 출처 변화와 제약별 그룹 분리, 잘못된 리뷰 요청 방지를 검증했다.
- 실제 로컬 API 보고서와 영어 desktop, 한국어 mobile, light theme를 확인했다.
  브라우저 전용 미실행 fixture와 보고서 503 응답도 점검했다. 페이지 오류와
  모바일 가로 넘침은 없었다. 이 검증은 새 ASIC·SPICE 평가를 실행하지 않았다.

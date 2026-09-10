# 풀리지 않은 문제들과 다음 작업 (2026-09-10)

이 서비스가 **아직 풀지 못한 문제**를 reference-db, self_improve 스캔, 케이스 파일,
리뷰 요청, 설계 스펙의 "Known limitations"에서 실제로 읽어낸 것만 정리한다.
숫자는 전부 `reference-db/cases/*.json`에서 이 날짜에 계산한 값이고, 해결책은
"시도할 만한 것"이지 "될 것"이 아니다 — 이 저장소의 `soul.md`대로, 돌려보기 전엔
unverified다.

이 머신에는 Docker도 PDK도 없다. 기록된 모든 런은 macOS(`/Users/kos2001/...`)
에서 나왔다. 그래서 아래 항목 대부분은 **러너가 있어야 진행**되고, 그 자체가
§7의 문제다.

## 진행 상황 (2026-09-10, 같은 날 착수)

착수 순서대로 진행한 결과. 측정은 전부 이 머신의 WSL 러너(§7 해결)에서
나왔고 케이스는 reference-db에 있다.

| # | 상태 | 무엇이 바뀌었나 |
|---|---|---|
| 7 | **해결** | WSL Ubuntu의 Docker를 러너로 세움. PDK 15 GB(sky130 + gf180mcu, 같은 volare 해시) 직접 내려받음(GitHub API 한도 우회). counter4 전체 플로우 45 s. 컨테이너가 root로 쓰는 run dir을 호스트에 돌려주는 `claim_run_dir()` 추가 |
| 2 | **해결** | `run_spec.json`의 `expected_outcome: "fail"`. cdc_twoclock은 "expected to fail (negative control)"로 분류되어 review 백로그·패턴 승격 후보에서 빠짐 |
| 11 | **해결** | Action Center 행과 매뉴얼이 설계별 실측 중앙값 소요시간을 표시(스토어에서 계산). `run_candidate()`가 모든 런에 `seconds`를 기록 |
| — | **발견·수정** | operating point가 후보의 `CLOCK_PERIOD` override가 아니라 config.json의 period로 계산되고 있었음: 저장된 84개(6개 설계)의 Fmax가 최대 2.5x 틀림. `run_clock_period()`로 고치고 `repair_operating_points.py`로 재계산, 이전 값을 기록으로 남김 |
| 10 | **해결** | 패턴 #4: setup만 실패한 완주 런은 자기 min_period × 1.05로 `CLOCK_PERIOD`를 올려 재시도. aes(11.2 → 12 ns)와 gcd@gf180(12 → 13.2 ns) 두 설계·두 기술에서 setup이 닫힘 |
| — | **발견·수정** | propose_repairs()가 수리 후보에서 `pdk`/`scl`을 떨어뜨려, gf180 후보의 수리 런이 sky130에서 돌고 "gf180 pass"로 기록될 뻔함(area 12,133 → 3,458 µm²가 단서). `_repaired()`가 기술을 복사 |
| 1 | **해결** | `gf180_drc.py`: OpenLane 2.3.10이 sky130 외에서 건너뛰는 KLayout DRC를 PDK가 싣는 GF180 룰덱으로 직접 실행. 덱의 `pmap` 로거(이미지에 없음)만 패치. gcd@gf180mcuD: 전체 덱 47 s, 0건 |
| 5 | **해결** | gcd@gf180mcuD **첫 pass** (`gcd__2026-09-10__035620`): DELAY 2, 13.2 ns, `MAX_FANOUT_CONSTRAINT 12`. 남았던 위반은 CTS 리프 버퍼의 fanout 11 > 10이었고 `CTS_SINK_CLUSTERING_SIZE` 10·8은 둘 다 11-sink 클러스터를 그대로 냄(목표값이지 상한이 아님) |
| 3 | 진행 중 | iter5: `RUN_POST_GRT_DESIGN_REPAIR`/`RESIZER_TIMING`을 켜도 hold 264 → 279/272, max-slew 570 → 532/545 — 빠진 스텝 문제가 아님. 코너별로 보면 max-slew 532/376/186은 세 ss 코너에만, hold도 ss에만. iter6(진행 중): 라이브러리 자체 한계인 `MAX_TRANSITION_CONSTRAINT 1.5`, 그리고 측정된 −0.32 ns를 덮는 hold margin |
| 4 | 진행 중 | SynthesisExploration(9개 전략, 한 번): AREA 3가 slack 최선, AREA 2가 면적 최선. iter2(진행 중): 측정된 22.45 ns × 1.05 = 23.6 ns, AREA 0/AREA 3, post-GRT repair 켬 |
| 6 | 진행 중 | G/H/I Magic 사다리 실행 중. 불리언 override는 counter4에서 검증됨(`40-openroad-repairdesignpostgrt` 실행 확인) |
| 8, 9 | 미착수 | 테스트벤치 4개; surrogate 재측정은 #1·#2 이후 |

## 요약표

| # | 문제 | 규모 (측정) | 성격 | 막힌 이유 |
|---|---|---|---|---|
| 1 | gf180mcu 런은 절대 pass할 수 없음 | 170런 / 0 pass, 139개가 `KLayout DRC error(s)` unverified | 구조적 (검증 게이트) | 메트릭이 gf180에서 생성 안 됨 |
| 2 | cdc_twoclock 은 절대 pass할 수 없음 | 105런 / 0 pass, 전부 `clk_b` 미제약 | 구조적 (SDC) | base.sdc가 첫 클럭만 제약 |
| 3 | aes: setup은 닫았지만 hold + max-slew DRV가 남음 | 12 ns에서 hold 264건, max-slew 431–570건 | 설계/툴 튜닝 | 시도한 knob(hold margin, hold-first)이 역효과 |
| 4 | riscv32i: 런 1개, WNS −2.45 ns | setup 367건, max-fanout 81건 | 설계 | 계획된 sweep이 한 번도 안 돌았음 |
| 5 | gcd@gf180: 모든 synth 전략에서 setup 실패 | WNS −1.17…−2.28 ns @10 ns | 잘못된 축을 sweep | period sweep을 gf180에서 안 돌림 (+ #1) |
| 6 | sram_wrapper: 블로커 2개, 계획만 있고 런 없음 | 3 케이스 / 0 pass | 데이터 부재 | Magic 사다리 G/H/I 미실행, OpenRAM 재특성화 미실행 |
| 7 | 이 머신에서 에이전트가 아무것도 못 돌림 | Docker 없음, 경로가 Mac 절대경로 | 인프라 | 원격 러너 없음 |
| 8 | Power는 9개 중 8개 설계에서 vectorless 추정치 | spm에서 44% 과소평가 확인 | 데이터 부재 | 테스트벤치가 spm에만 있음 |
| 9 | surrogate의 pass/fail 정확도 65% | area 99% / power 98% / pass 65% | 데이터 라벨 | #1·#2 때문에 라벨이 툴 갭에 지배됨 |
| 10 | 자동 수리 패턴 3개뿐, 타이밍 실패엔 0개 | OPEN 설계 커버리지 0/2, 0/6, 0/5, 0/1 | 자동화 갭 | 타이밍 위반 패턴이 없음 |

---

## 1. gf180mcu: 검증 게이트가 기술 하나를 통째로 막고 있다

**측정.** pdk별 집계 (`iterations[].results[].verdict`):

    sky130A     284런  136 pass
    gf180mcuD   162런    0 pass   unverified: KLayout DRC error(s) ×131
    gf180mcuC     8런    0 pass   unverified: KLayout DRC error(s) ×8

gf180 런의 `step_coverage.missing`에 `KLayout.DRC`가 **없다** — 즉 스텝은
실행됐는데 `metrics.json`에 `klayout__drc_error__count`가 없다.
`orchestrator.py`의 `SIGNOFF_METRICS`는 메트릭 부재를 "never checked" →
`unverified`로 처리하고, `unverified`는 pass를 막는다 (`passed = not
violations and not unverified`, orchestrator.py:459, :715). 게이트 로직 자체는
옳다. 문제는 **OpenLane 2.3.10의 `KLayout.DRC`가 sky130 전용**이어서 gf180에선
아무 숫자도 안 내놓는다는 것이고, 그 결과 `tech_compare.py`가 하는 "DTCO의
기술 절반"이 한 번도 signoff에 도달한 적이 없다.

counter4@gf180 thin은 덤으로 `utilization 0.814 > 0.75`도 걸리는데,
`FP_CORE_UTIL 25`에서도 0.814라 utilization 위반이 아니라 작은 설계의
site-row 양자화다. 별개 문제지만 같은 설계가 두 이유로 못 닫힌다.

**해결책 (순서대로).**

1. 핀된 이미지에서 확인: `openlane --list-steps` / `KLayout.DRC` 소스에 PDK
   가드가 있는지. gf180 PDK는 자체 KLayout DRC 덱을 싣는다
   (`gf180mcu*/libs.tech/klayout/drc/`). 있다면 OpenLane 스텝을 우회해
   이미지에 이미 있는 `klayout -b -r <deck>.drc`를 직접 돌리고 그 카운트를
   메트릭으로 채운다 — `equiv_check.py`가 고장난 `Yosys.EQY`를 대체한 것과
   같은 전례.
2. 덱이 없거나 못 돌리면, verdict를 **기술 인식형**으로: "이 PDK에서는 이
   체크가 존재하지 않는다"를 `unverified`와 구분되는 `not_applicable`로
   기록한다. 단, Magic DRC + LVS는 여전히 요구. 안 그러면 gf180은 영원히
   실패 레이블만 쌓고 surrogate(§9)를 오염시킨다.
3. 완료 조건: gf180 케이스 하나가 `passed: true`로 reference-db에 기록되는 것.

## 2. cdc_twoclock: 의도된 negative control이 백로그를 점유한다

**측정.** 105런, 0 pass, 전부 `timing for clock domain 'clk_b' (declared but
never constrained)`. 설계 자체가 CDC 게이트가 실패할 수 있음을 증명하려고
만든 것(`cdc_twoclock.v` 헤더)이라 실패가 정답인데, `self_improve.py`는 매번
"OPEN, needs review"로 올리고 review request가 4개째 쌓여 있다.

**해결책.**

1. `spm`이 이미 하는 방식대로 `PNR_SDC_FILE`/`SIGNOFF_SDC_FILE`에 두 개의
   `create_clock` + `set_clock_groups -asynchronous`를 넣는다. 그러면
   `clk_b` 도메인이 실제로 분석되고, `cdc_check.py`의 `custom_sdc: true`
   경로로 unverified가 사라진다.
2. 그 다음 **구조적 CDC 체크**를 `cdc_check.py`에 추가한다 — 지금 docstring이
   "하지 않는다"고 명시한 부분. `netlist_graph.py`가 이미 있으니, 클럭이
   다른 flop→flop 경로 중 2단 동기화기 없는 것을 찾는 것은 그래프 탐색이다.
   이 설계에선 정확히 `ctr_a → capt_b` 8개가 잡혀야 하고, 그게 "게이트가
   실패할 수 있다"는 원래 목적을 **진짜 위반**으로 달성한다.
3. `run_spec.json`에 `expected_outcome: "fail"` 같은 표식을 두어
   `self_improve.py`가 negative control을 review 백로그에서 제외하게 한다.

## 3. aes: setup은 닫혔고, hold와 slew DRV가 남았다

**측정** (`aes__2026-08-30__145637.json`, `SYNTH_STRATEGY: DELAY 0`):

    CLOCK_PERIOD 11.2   setup 3 / hold 36  / max-slew 431 / WNS −0.253 (max_ss)
    CLOCK_PERIOD 12     setup 0 / hold 264 / max-slew 570 / hold WNS −0.318 (max_ss)

hold 위반이 `*_ss_100C_1v60` 세 코너에만 있고 ff/tt 6개 코너는 0이다.
hold margin 상향(0.3/0.25)과 `FIX_HOLD_FIRST`는 둘 다 악화시켰다
(iter3). 이전 리뷰 판정: "actionable, not exhausted".

**아직 안 해본 것.** 이 저장소 어디에도 `RUN_POST_GRT_DESIGN_REPAIR`,
`RUN_POST_GRT_RESIZER_TIMING`을 켠 run_spec/config가 없다.
`step_coverage`가 매 런마다 "꺼져 있음"으로 기록만 한다. `RepairDesignPostGRT`
는 정확히 라우팅 후 max-slew/cap DRV를 고치는 스텝이고, 431–570건의 max-slew가
ss 코너 지연을 늘리는 유일하게 일관된 인과 서술이다(리뷰의 결론).

**해결책 (사다리, 변수 하나씩).**

1. `iter5-postgrt`: period 12 + `RUN_POST_GRT_DESIGN_REPAIR=true`. max-slew가
   무너지는지만 본다.
2. `iter5-postgrt-timing`: + `RUN_POST_GRT_RESIZER_TIMING=true`. hold 264 →?
3. 리사이저가 hold를 어느 코너에서 고치는지 `resolved.json`에서 읽는다.
   ss에만 hold가 남는 것은 CTS skew가 ss에서 커진다는 뜻이므로, 그 다음
   후보는 CTS 쪽(`CTS_CLK_BUFFERS`, 클러스터링)이지 hold margin이 아니다.
4. `sta_path.query(..., 'report_checks -path_delay min -corner max_ss_100C_1v60')`
   로 최악 hold 경로 하나를 실제로 읽는다 — sram_wrapper에서 "knob 5번
   sweep보다 STA에 한 번 묻는 게 빨랐다"는 교훈이 tool_retrieval에 있다.

## 4. riscv32i: 사실상 측정이 없다

**측정.** 케이스 1개, 후보 1개(`c-hd-clock_period20`, 킬된 배치에서 복구됨):
setup 367건, WNS −2.45 ns @20 ns, max-fanout 81건, util 0.575.
`run_spec.json`의 sweep(12/20/30)은 **한 번도 실행되지 않았다**. surrogate가
평가 가능해지려면 9개 샘플이 필요한데 1개다.

**원인 후보 (RTL에서 읽음).** `regfile.v`는 32×32 flop + 조합 32:1 읽기 mux
2개, `dmem.v`/`rom.v`는 flop/case 배열이다. max-fanout 81건은 이런 구조의
디코드 신호에서 나온다.

**해결책.**

1. `synth_explore.py`(9초)로 9개 전략을 먼저 재고, 그 다음 period sweep을
   실제로 돌린다. 30 ns에서도 못 닫으면 설계 문제로 확정.
2. `MAX_FANOUT_CONSTRAINT`를 명시 후보로 (현재 어느 설계도 override 안 함).
3. 장기적으로는 sram_wrapper §6의 "제3의 길"과 같은 질문이다: 메모리를
   flop 배열로 둘지(DFFRAM), 매크로로 뺄지.

## 5. gcd@gf180: 잘못된 축을 sweep했다

**측정.** `gcd__2026-08-30__045044.json`: `SYNTH_STRATEGY` DELAY 0–4 전부
setup 실패, WNS −1.17 ~ −2.28 ns @ `CLOCK_PERIOD 10`. 같은 설계가 sky130에선
통과했다. gf180 5V 셀은 sky130 hd보다 수 배 느린데, `run_spec.json`의
period sweep(3/5/8/12/20)은 sky130에서만 돌았다.

**해결책.** gf180에서 `CLOCK_PERIOD` sweep을 돌린다. tool_retrieval의
"re-sweep the floorplan rather than reusing the old one"(die는 기술마다
다시 잰다)이 clock에도 그대로 적용된다 — 그 measurement 항목에 clock을
추가하면 다음 설계에서 같은 실수를 retrieval이 막는다. 단 #1이 먼저
풀리지 않으면 통과해도 unverified다.

## 6. sram_wrapper: 계획은 둘 다 있고, 런은 없다

케이스 진단에 이미 정리돼 있다. 남은 것은 실행이다.

- **Magic 블로커**: `run_spec.json`의 G-capture / H-lefdrc / I-klayoutgds
  사다리. `MAGIC_DRC_USE_GDS=false`는 OpenLane이 OpenRAM sky130 매크로에
  문서화한 우회이고 저장소에서 한 번도 안 써봤다. 위험 하나: 이 저장소의 첫
  boolean override라 `override_value()`가 `"false"`로 보내는 게 OpenLane
  파서에 먹는지 미확인 — 안 먹으면 `config.json`으로 옮기고
  `reject_ignored_overrides()`가 잡는지 본다.
- **특성화 천장 0.04 ns**: `2026-09-02-sram-characterisation-data-plan.md`.
  OpenRAM 설정 두 줄(`analytical_delay = False`, `slew_scales = [0.25, 1, 8,
  48]`)로 재생성, 4단계 검증(ceiling ≥ 0.24, 기존 3점 보존, DRC/LVS, RSZ-0090
  소멸). 8192비트라 OpenRAM 자체 "느림" 경계(2^14) 아래.
- **열린 질문**: `repair_design`이 왜 `xnor2_2`를 버퍼 없이 매크로 입력에
  남기는가. `sta_path.query`로 `report_checks -to u_sram/addr1[1]` +
  `odb_query`로 답할 수 있는 질문이고, 새 런이 필요 없다(기존 run dir이
  Mac에 남아 있다면).
- **목표 결정**: DFFRAM/ORRAM으로 매크로를 없애면 두 블로커가 같이 사라지지만
  "macro-heavy topology 샘플"이라는 이 설계의 존재 이유도 사라진다. 기술
  질문이 아니라 목표 질문이라 케이스에 기록만 돼 있다 — 결정이 필요하다.

## 7. 러너가 없다: 에이전트가 이 머신에서 행동할 수 없다

`docker: command not found`, `pdk/` 없음, `pipeline/designs/*/runs/` 없음.
reference-db의 `data.verification.metrics_json`, `layout.gds` 등은 전부
`/Users/kos2001/gitspace/...` 절대경로라 `ppa_sta_report`, `ppa_odb_query`,
`render_layout`은 다른 머신에서 전부 실패한다. soul.md가 대시보드를 "제어
표면"이라 부르지만, 지금 이 체크아웃에선 "run agent now"가 아무것도 못 한다.

**해결책.**

1. `run_stage.py`에 실행 백엔드를 하나 더: 로컬 Docker 또는 `ssh <host>`
   (Mac). `toolchain.py`가 이미 host 정보를 기록하니 `host.remote`를 붙인다.
   대시보드의 `POST /pipeline/run`은 그대로 두고 아래에서만 바뀐다.
2. 또는 이 Windows 머신에 WSL2 + Docker Desktop + `volare`. 문서에 없는
   플랫폼이라 첫 런에서 무엇이 깨지는지 자체가 기록할 가치가 있다.
3. 케이스의 절대경로를 저장소 기준 상대경로로 바꾸거나, 승자 후보의
   `final/`(metrics.json, odb, sdf, spef)을 `reference-db/artifacts/`에 보관.
   `layouts/`가 GDS 렌더를 보관하는 이유와 같다 — run dir은 gitignore라
   사라진다.

## 8. Power 숫자의 8/9는 추정치다

스펙 Known limitations 그대로: activity 기반 power는 테스트벤치가 필요하고
`spm`에만 있다(`designs/spm/verify/spm_tb.v`). spm에서 vectorless가 조합
power를 **44% 과소평가**했음이 측정돼 있다. 도구는 이미 있다
(`power_activity.py`); 없는 것은 파일 4개다.

**해결책.** counter4(사소), gcd(입력 벡터 몇 개), aes(opencores 원본
testbench가 있음 — 벡터 재사용), riscv32i(`rom.v`에 프로그램이 있어
자기 구동). 이건 툴링이 아니라 테스트벤치 작성이고, PPA의 "P"를 측정치로
바꾸는 가장 싼 일이다.

## 9. surrogate의 pass/fail 65%는 모델 문제가 아닐 수 있다

`surrogate.py`: area 99%, power 98%, pass/fail 65%
(`tests/test_learned_model_fit.py`가 고정). 그런데 454런 중 275개(gf180 170 +
cdc 105)는 **설계와 무관하게** pass가 불가능한 라벨이다. pass/fail 모델은
"이 설정이 좋은가"가 아니라 "이 PDK인가/이 설계인가"를 배우고 있다.

**해결책.** #1과 #2를 먼저 고치고 나서 재측정한다. 그 전에 샘플을 더 모으는
건 같은 편향을 더 쌓는 일이다. `test_learned_model_fit.py`가 고정한 65%가
움직이는지가 #1/#2의 부수 검증이 된다.

## 10. 자동 수리는 세 패턴이고, 지금 가장 흔한 실패엔 하나도 없다

`propose_repairs()`의 패턴: PDN strap(FP_CORE_UTIL↓), die too small(DIE_AREA×2),
utilization overshoot(FP_CORE_UTIL↓). 전부 floorplan/PDN 단계의 error 텍스트다.
OPEN 설계의 커버리지는 aes 0/2, cdc 0/6, gcd 0/5, riscv 0/1 — reference-db에서
지금 가장 흔한 실패는 **완주 후 타이밍 위반**인데 여기에 패턴이 없다.

**승격 후보 (측정에 근거한 것만).** aes iter4가 보여준 것: setup은
`CLOCK_PERIOD ≥ ss 코너 min_period`가 되면 닫힌다(11.2 → −0.25 실패, 12 →
0). `operating_point.py`가 이미 `min_period_ns`를 코너별로 계산한다. 따라서
"setup 위반 + 측정된 `min_period_ns`가 있으면 `CLOCK_PERIOD = ceil(min_period
× 1.05)`로 한 번 재시도"는 추측이 아니라 측정에서 유도된 수리다. 단서: hold는
period와 무관하므로 이 패턴은 setup만 주장하고, hold가 남으면 그대로
escalate한다. gcd@gf180(#5)에서 같은 패턴이 두 번째로 확인되면 승격 조건
("proven, not assumed")을 만족한다.

## 11. 작은 것들 (스펙 Known limitations에서, 여전히 열려 있음)

- `pick_winner()`: 후보 하나만 측정치가 없어도 전체가 추정치로 떨어진다.
  그 후보만 제외하면 된다.
- `config.json`의 키는 무시 여부를 검사하지 않는다(`reject_ignored_overrides`
  는 CLI override만). `PDN_MACRO_CONNECTIONS` 오타가 그렇게 숨어 있었다.
- 번역/진단이 한 덩어리로 3–4분 걸린다. 청크 단위 요청 또는 빠른 모델 폴백.
- `feedback.jsonl`의 유일한 항목(2026-08-28): "큰 설계에서 전체 런이 얼마나
  걸리는지 매뉴얼로는 알 수 없다." `collect.py`가 `seconds`를 결과마다
  기록하고 `recorded_seconds()`로 설계별 평균도 내는데, 매뉴얼/Action Center에
  안 보인다. 데이터는 있고 표시만 없다.

## 착수 순서 제안

러너(#7)가 없으면 아무것도 측정할 수 없으므로 그것이 0번이다. 그 다음은
**런 없이 되는 것**부터: #2-3(expected_outcome 표식), #11(seconds 표시,
pick_winner), #10의 패턴 코드와 테스트, #1-2(verdict의 not_applicable 구분).
러너가 생기면 #1-1(gf180 KLayout 덱 직접 실행) → #3 사다리 → #5 → #4 →
#6 순으로, 각각 케이스 하나가 reference-db에 기록되는 것을 완료 조건으로.

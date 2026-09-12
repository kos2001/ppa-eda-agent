# Virtuoso의 오픈소스 대응과 custom bridge (2026-09-12)

세 가지 질문에 답한다.

1. 이 저장소를 Siemens/Synopsys/Cadence 상용 툴과 유사하게 만들려면 무엇이 비어 있나
2. Cadence Virtuoso에 대응하는 오픈소스 프로젝트는 무엇인가
3. `virtuoso-bridge-lite`를 여기에 적용할 수 있나

측정은 전부 이 머신(macOS, `/Users/kos2001/gitspace/ppa-eda-agent`)에서
2026-09-12에 직접 실행한 값이다. 돌려보지 않은 것은 돌려보지 않았다고 쓴다
(`soul.md`).

## 1. 무엇이 비어 있나 — 이 저장소는 상용 플로우의 "디지털 절반"이다

상용 EDA는 두 개의 플로우다. RTL부터 GDS까지 가는 **디지털 구현 플로우**와,
트랜지스터를 직접 그리는 **커스텀/아날로그 플로우**. 이 저장소가 가진 것은
전자뿐이다.

| 상용 툴 | 이 저장소의 대응 | 상태 |
|---|---|---|
| Synopsys Design Compiler | Yosys (`synth_explore.py`) | 실런 |
| Cadence Innovus / Synopsys Fusion Compiler | OpenROAD via OpenLane2 (`run_stage.py`) | 실런 |
| Synopsys PrimeTime | OpenSTA (`sta_report.py`, `sta_path.py`) | 실런 |
| Synopsys PrimePower | OpenSTA + `power_activity.py` | 실런 (9개 중 1개만 벡터 기반) |
| Siemens Calibre DRC/LVS | KLayout DRC, Magic DRC, netgen LVS | 실런 |
| Synopsys Formality | Yosys SAT (`equiv_check.py`) | 실런 |
| Cadence Innovus AutoTuner / Synopsys DSO.ai | `orchestrator.py` sweep + `propose_repairs()` + `surrogate.py` | 실런 |
| **Cadence Virtuoso (schematic/layout/ADE)** | **없었음** | **이번 작업** |
| Cadence Spectre | 없었음 → `custom_bridge.run_spice()` | 이번 작업 |
| Siemens Calibre xRC / Cadence Quantus (PEX) | 없음 | 미착수 |
| Cadence Liberate (특성화) | 없음 (`recharacterise.py`는 천장을 *보고*만 한다) | 미착수 |

즉 "상용 툴처럼"의 가장 큰 구멍은 기능 하나가 아니라 **플로우 하나**였다.
디지털 쪽은 이미 상용 대응물이 전부 실런으로 붙어 있고, 커스텀/아날로그 쪽은
`sim/`의 OpenSTA뿐이었다 — OpenSTA에는 트랜지스터가 한 개도 없다.

## 2. Virtuoso에 대응하는 오픈소스

**단일 대응물은 없다.** Virtuoso는 하나의 프로그램 안에 schematic capture,
custom layout, 시뮬레이션 환경, 라이브러리 관리가 들어 있는 통합 환경이고,
오픈소스 쪽은 같은 일을 **다섯 개의 프로그램**이 나눠 한다. 이 사실 자체가
설계에 영향을 준다(§4의 `evaluate(backend=...)`가 backend를 인자로 받는 이유).

### 2.1 사실상의 표준: Open Circuit Design 스택

| Virtuoso의 구성요소 | 오픈소스 | 배치 언어 | 이 머신 |
|---|---|---|---|
| Virtuoso Schematic Editor (Composer) | **Xschem** | Tcl | 미설치 |
| Virtuoso Layout Suite (폴리곤 편집) | **Magic** | Tcl | OpenLane 이미지로 실행됨 |
| Layout 뷰어 + PVS/Calibre 룰덱 실행 | **KLayout** | Python/Ruby | OpenLane 이미지로 실행됨 (로컬 미설치, §4 주의) |
| ADE Explorer / Spectre | **ngspice** (또는 Xyce) | `.control` 블록 | **ngspice-46 설치됨** |
| Assura / PVS LVS | **netgen** | Tcl | **1.5.323 설치됨** |

이 조합이 "오픈소스 Virtuoso"로 통하는 이유는 취향이 아니라 **PDK가 그렇게
배포되기 때문**이다. 이 저장소의 `pdk/` 안에 이미 들어 있다:

    pdk/sky130A/libs.tech/{xschem,magic,klayout,netgen,ngspice,xcircuit,irsim,qflow}
    pdk/gf180mcuD/libs.tech/{xschem,magic,klayout,netgen,ngspice,xyce}

`pdk/sky130A/libs.tech/xschem/sky130_fd_pr/`에는 76개의 심볼 파일(`*.sym`)
(`nfet_01v8.sym`, `cap_mim_m3_1.sym`, …)이 있다. 즉 커스텀 플로우에 필요한
PDK 자산은 이미 이 저장소 안에 있었고, 그것을 부르는 코드만 없었다.

배포판으로 묶은 것이 **IIC-OSIC-TOOLS**(hpretl) 도커 이미지 — 위 다섯 개에
OpenVAF(Verilog-A 컴파일러, Cadence AMS Designer 대응)와 gaw(파형 뷰어)까지
한 이미지에 들어 있다. xschem과 magic이 필요해지면 새 이미지를 만들 게 아니라
이것을 쓰는 것이 맞다.

### 2.2 같은 자리를 노리는 다른 프로젝트들 (그리고 왜 1차 선택이 아닌가)

- **Electric VLSI** — schematic+layout+시뮬레이션이 한 앱에 들어간, 구조상
  Virtuoso에 가장 가까운 단일 프로그램. 그러나 sky130/gf180 PDK가 Electric
  형식으로 배포되지 않는다. 통합도를 얻고 PDK 지원을 잃는 교환이라 여기서는
  손해다.
- **GLayout / OpenFASoC** (VLSIDA·idea-fasoc) — 파라미터로 아날로그 레이아웃을
  생성한다. Virtuoso Layout XL의 modgen/제약 기반 배치에 대응하는 쪽이지,
  편집기 대응이 아니다. 생성기 계열이 필요해지는 시점은 이 저장소가 아날로그
  셀을 *탐색*하기 시작할 때다(지금은 셀 하나).
- **gdsfactory / gdstk / hdl21** — 레이아웃·회로를 코드로 기술. 편집기가 아니라
  스크립팅 계층이고, KLayout Python API와 역할이 겹친다.
- **BAG3** (BAG_framework) — 흥미로운 대조군: 이것은 Virtuoso를 *대체*하지 않고
  SKILL로 **구동**한다. 즉 `virtuoso-bridge-lite`와 같은 계열이며, Virtuoso
  라이선스가 있는 환경에서만 의미가 있다.
- **XCircuit** — sky130A가 tech 파일을 싣지만(`libs.tech/xcircuit`) schematic
  전용이고, PDK 심볼 커버리지는 xschem 쪽이 압도적이다.

결론: **xschem + magic + klayout + ngspice + netgen**이 대응물이고, 이유는
PDK가 그 다섯 개를 지원하기 때문이다.

## 3. `virtuoso-bridge-lite`를 적용할 수 있나

`~/gitspace/virtuso_bridge/virtuoso-bridge-lite`
(github.com/Arcadia-1/virtuoso-bridge-lite, MIT, v0.7.0, 약 15,000줄).
에이전트가 SSH 터널 너머의 진짜 Virtuoso에 SKILL을 실행시키고, Maestro/Spectre
결과를 타입 있는 `VirtuosoResult`로 되받는 패키지다.

**측정.** 소스에서 스크래치 venv에 설치해 실제로 실행했다:

    virtuoso-bridge status   -> "No profiles found. Set VB_REMOTE_HOST in .env first."
    virtuoso-bridge license  -> "VB_CADENCE_CSHRC is not set."
    command -v virtuoso      -> not found
    command -v spectre       -> not found

**판정 세 줄.**

1. **그대로는 못 쓴다.** 두 백엔드(SKILL over SSH, standalone Spectre)가 모두
   Cadence 소프트웨어와 라이선스 서버를 요구한다. 이 머신에는 둘 다 없다.
   이것은 패키지의 결함이 아니라 환경에 대한 사실이다 — Virtuoso 세션이 있는
   호스트에서는 이것이 맞는 도구이고, 여기서 만든 것이 그것을 대체하지 않는다.
2. **인터페이스는 적용된다.** `src/virtuoso_bridge/models.py`가
   `VirtuosoInterface`를 ABC로 선언하고(`ensure_ready` / `execute_skill` /
   `test_connection`) 결과를 `VirtuosoResult(status, output, errors, warnings,
   execution_time, metadata)`로 돌려준다. 패키지 자신이 백엔드 교체를 전제로
   설계돼 있다는 뜻이다. `pipeline/custom_bridge.py`는 그 계약을 필드 단위로
   복제한다 — pydantic과 SSH 터널은 가져오지 않는다. `soul.md`의 "borrow the
   working part, not the whole machine".
   - 구체적으로: `ExecutionStatus`의 네 값(success/failure/partial/error)을
     그대로 쓰고, "툴이 설치돼 있지 않음"에 다섯 번째 값을 만들지 **않는다**.
     ERROR + `metadata["reason"] = "not_installed"`로 표현한다. 다섯 번째 값이
     더 정확하지만, 상용 브리지에 맞춰 쓰인 호출자를 깨뜨리는 유일한 변경이
     바로 그것이다.
3. **언제 도로 꺼내 쓰나.** (a) Virtuoso가 있는 서버에 SSH가 되고, (b) 그
   서버에서 `load(...)`를 CIW에 넣을 수 있고, (c) 라이선스가 나오는 순간,
   `~/.virtuoso-bridge/.env` 하나 만들어 붙이면 된다. 같은 에이전트 스킬이
   양쪽 백엔드에서 동작하도록 결과 계약을 맞춰 둔 것이 그 대비다.

## 4. 이번에 구현한 것

`pipeline/custom_bridge.py` — 오픈소스 커스텀 스택에 대한 의존성 없는 브리지.

- `status()` — 다섯 백엔드 각각이 이 호스트에서 실제로 실행 가능한지, 그리고
  각각이 어떤 Cadence 툴에 대응하는지. `virtuoso-bridge status`의 대응물.
- `evaluate(backend, code)` — 백엔드의 자기 배치 언어로 스크립트 실행.
  `execute_skill`의 대응물. backend가 **인자인 이유는 §2**: SKILL은 Virtuoso가
  한 프로그램이라 하나의 언어지만, 여기서는 다섯 프로그램·세 언어다.
- `run_spice(netlist, pdk, corner)` — 진짜 트랜지스터 레벨 시뮬레이션. 덱 안의
  `%PDK_LIB%` 토큰을 PDK/코너의 실제 모델 라이브러리로 치환하고, `.meas`
  결과를 `metadata["measurements"]`로 파싱해 돌려준다.
- MCP 툴 3개: `ppa_custom_status`, `ppa_custom_eval`, `ppa_spice_sim`.

`pipeline/analog/inv/inv.spice` — 이 저장소 최초의 트랜지스터 레벨 셀.

**실측 (sky130A, W_p=1.0 / W_n=0.5 / L=0.15 µm, VDD=1.8 V, C_L=10 fF):**

| corner | vtrip (V) | tphl (ps) | tplh (ps) |
|---|---|---|---|
| ff | 0.837 | 53.3 | 64.4 |
| tt | 0.867 | 66.3 | 80.5 |
| ss | 0.895 | 85.5 | 107.1 |

ff→ss 지연 1.60x / 1.66x. Docker 없이, 라이선스 없이, mock 없이 이 머신의
ngspice-46과 `pdk/sky130A`의 실제 소자 모델로 나온 값이다.

### 올라오면서 실제로 밟은 것들 (테스트가 지키는 내용)

- ngspice는 **`.meas`가 실패해도 exit 0**으로 끝난다. 첫 시도에서
  `meas dc vtrip ... rise=1 failed!`가 찍히고도 rc=0이었다. 그래서
  `classify()`는 rc만 보지 않고, rc=0 + 에러줄 = `partial`로 분류한다.
  이것이 PARTIAL이 존재하는 이유 전부다.
- `.meas ... trig/targ` 형식은 `tphl = 6.63e-11 targ= ... trig= ...`처럼
  뒤에 컨텍스트 필드를 붙여 출력한다. 줄 끝에 앵커를 건 정규식은 DC 측정 하나만
  돌려주고 지연값 전부를 **아무 신호 없이** 누락시켰다.
- `.control` 블록 안에서는 `{VDD/2}` 같은 param 치환이 동작하지 않는다.
  ngspice가 해결 불가능한 `vexprint` 벡터로 바꾼 뒤 measure 실패로 처리하고,
  역시 exit 0.
- `netgen -batch FILE`은 stdin을 읽으며 멈춘다. 올바른 형태는
  `netgen -batch source FILE`. 이 세션의 셸을 2분간 잡아먹고 알아냈다.
- **gf180mcu의 typical 섹션 이름은 `tt`가 아니라 `typical`**이다
  (`sm141064.ngspice`에서 직접 확인). 이름이 틀린 덱은 에러를 내지 않고
  *다른 실리콘을* 시뮬레이션한다. 그래서 코너 바인딩을 호출자에게 맡기지 않는다.
- `/Applications/KLayout`은 존재하지만 안에 바이너리가 없다(`MacStdUser-
  ReadMeFirst` 폴더). 그래서 툴 탐색은 "알려진 설치 경로가 있으면 설치된 것"이
  아니라 실행 파일 자체를 찾는다.

## 4.1 시작은 schematic이다 (같은 날, 지적받고 고침)

첫 구현은 `run_spice()`와 손으로 쓴 덱에서 시작했다. 그건 커스텀 플로우가
**실제로 시작하는 곳보다 한 단계 아래**다. Virtuoso에서 아무도 넷리스트를
타이핑하지 않는다 — schematic을 그리고 툴이 넷리스트를 쓴다. 덱은 산출물이고,
그걸 소스로 취급하면 schematic과 넷리스트가 갈라진다.

**이게 미학 문제가 아니라는 것은 측정됐다.** 같은 인버터를 두 경로로 돌린 결과:

| corner | vtrip 손 (V) | vtrip sch (V) | tphl 손 (ps) | tphl sch (ps) | 차이 |
|---|---|---|---|---|---|
| ff | 0.837131 | 0.837141 | 53.34 | 54.68 | +2.5% |
| tt | 0.867162 | 0.867167 | 66.32 | 68.21 | +2.9% |
| ss | 0.894842 | 0.894843 | 85.48 | 88.27 | +3.3% |

vtrip은 여섯 자리까지 같다 — DC 동작점은 같은 회로다. 지연은 다르다.
schematic 경로의 디바이스는 PDK의 심볼이 모든 인스턴스에 붙이는 실제 확산
기생분(`ad`/`pd`/`as`/`ps`/`nrd`/`nrs`)을 달고 나오고, 손으로 쓴 덱에는 그게
없다. **맞는 숫자는 넷리스트된 쪽이고, 그 숫자는 툴이 만들어야만 존재한다.**

### xschem을 실제로 돌리기까지

`status()`가 "xschem 미설치"라고 보고만 하는 상태로는 진입점이 닫히지 않는다.
세 경로를 재보고 셋째를 골랐다:

1. **Homebrew** — formula 없음 (`brew info xschem` → no available formula).
2. **macOS 소스 빌드** — xschem 자신의 `README_MacOS.md`가 XQuartz와 **X11에
   링크된 Tk**를 요구한다. 이 머신의 brew tcl-tk는 Aqua 링크다. 툴보다 의존성이
   큰 우회.
3. **자체 이미지** — Debian trixie가 xschem **3.4.4**를 패키징한다(bookworm은
   2.8.1로 sky130 심볼 라이브러리에 오래됨). `debian:trixie-slim` + apt 패키지
   하나 = `pipeline/docker/xschem.Dockerfile`. IIC-OSIC-TOOLS는 넷리스터 하나
   돌리자고 받기엔 크다.

`custom_bridge.ensure_xschem_image()`가 없으면 첫 사용 시 빌드한다.

### 실제로 밟은 것 (테스트가 지킴)

- **PDK 예제로 먼저 검증했다.** 내 schematic을 쓰기 전에 PDK 자신의
  `sky130_tests/passgate.sch`를 넷리스트해서 배선이 맞는지 확인했다. 디바이스
  인스턴스 관용구(`ad`/`pd`/... , `spiceprefix=X`)도 거기서 그대로 가져왔다 —
  지어내지 않았다.
- **`-x`(no X) 없이는 컨테이너에서 시작조차 못 한다.** 넷리스터는 디스플레이가
  필요 없는데, 플래그가 없으면 X11 소켓이 없는 컨테이너에서 죽는다.
- **`$::SKYWATER_MODELS`를 쓰면 안 된다.** xschem의 기본 관용구는 넷리스트
  시점에 그걸 치환하는데, 그러면 코너 하나와 **컨테이너 경로**(`/pdk/...`)가
  함께 박힌다. 첫 넷리스트된 덱은 호스트 ngspice에서 정확히 그 이유로 죽었다
  (`Could not find library file /pdk/sky130A/...`). 그래서 schematic은
  `%PDK_LIB%` 토큰을 유지하고 `bind_corner()`가 런타임에 푼다 — 하나의
  schematic으로 코너를 스윕할 수 있는 이유이기도 하다.
- **exit code는 판정이 아니다.** 심볼을 못 찾으면 xschem은 아무것도 안 쓰고
  0으로 끝난다. 그래서 판정은 파일의 존재로 한다.
- **`inv.spice`는 생성물의 경로다.** 손으로 쓴 덱을 거기 두면 조용히 덮어쓰인다
  → `inv_handwritten.spice`로 개명하고 비교 기준으로 보존.

### 지금의 진입점

```sh
python3 pipeline/custom_bridge.py netlist --schematic pipeline/analog/inv/inv_tb.sch
python3 pipeline/custom_bridge.py spice --netlist pipeline/analog/inv/inv_tb.spice --corner ss
```

`pipeline/analog/inv/`: `inv.sch`(셀) → `inv.sym`(xschem이 `make_symbol`로 생성)
→ `inv_tb.sch`(테스트벤치, 셀을 인스턴스화). 계층이 요점이다 — 자극은
테스트벤치에, 회로는 셀에 있어서 한쪽을 스윕해도 다른 쪽을 건드리지 않는다.

## 4.2 그리고 보여준다

schematic에서 시작한다고 해놓고 그걸 볼 수 없으면 믿고 쓰라는 말이 된다. 이
저장소의 디지털 절반은 이미 같은 이유로 실제 GDS를 렌더한다(`render_layout.py`,
KLayout). 커스텀 절반에 같은 것을 붙였다.

- `custom_bridge.render_schematic()` — **xschem 자신의 SVG 내보내기**. 여기서
  `.sch`를 다시 그리는 두 번째 렌더러를 쓰지 않는 이유: 넷리스트와 어긋날 수 있는
  것이 하나 더 생긴다. 하나의 소스가 그림과 넷리스트를 같이 낸다는 게 요점이다.
- 대시보드 **회로도 탭**: 셀 목록 → 도면 → 코너 선택 → `netlist & simulate`.
  보고 있는 그림을 그 자리에서 돌린다(soul.md: 뷰어가 아니라 제어 표면).
  `GET /analog/cells`, `GET /analog/svg`, `POST /analog/run`.
- MCP: `ppa_render_schematic`.

### 여기서도 실측으로 걸린 것

- **viewBox가 없다.** xschem은 `width=1000 height=700`만 쓰고 viewBox를 안
  넣어서, 패널에 넣으면 1000x700 고정이고 확대/축소에 반응하지 않는다.
  `add_viewbox()`가 기존 속성을 건드리지 않고 viewBox만 덧붙인다 — 파일은 여전히
  단독으로 자기 크기로 열린다.
- **`print svg`는 현재 창을 내보낸다.** 기본 창이 도면 범위가 아니라서 첫
  내보내기는 테스트벤치의 `.control` 텍스트를 오른쪽에서 잘라먹었다.
  `zoom_full`을 먼저 준다. (`svg_full`은 없다 — `pdf_full`만 있다.)
- **분석 블록은 도면에 펼치지 않는다.** 열 줄짜리 `.control`은 1000px 캔버스를
  넘어간다. `code_shown.sym` → `code.sym`으로 바꿔 아이콘으로 접었고, **넷리스트가
  바이트 단위로 동일함을 확인**했다. 상용 ADE도 분석 설정을 도면 위에 인쇄하지
  않는다.
- **라벨이 심볼 위에 겹쳤다.** 소스/부하 단자에 바로 붙인 net label을 20단위 스텁
  위로 옮겼다. 여기서도 넷리스트 동일성을 diff로 확인했다 — 배선을 만지면서
  연결이 바뀌지 않았다는 증거가 필요하다.
- **트랜스크립트가 mount 이후로 갱신되지 않고 있었다.** `useSyncExternalStore`는
  스냅샷을 **참조로** 비교하는데 `getEntries()`가 같은 배열을 계속 돌려줬다.
  회로도 탭에서 시뮬레이션을 돌리고 콘솔이 움직이지 않는 것을 보고 발견했다 —
  즉, 이 탭을 만들다가 콘솔의 실제 버그를 잡았다. 스냅샷을 복사본으로 바꿨다.

남은 것: 셀 도면(`inv.sch`)은 디바이스 주석 텍스트가 회전 배치 때문에 서로
겹친다. 토폴로지·핀·넷은 정확하고(넷리스트가 증거), 겹치는 것은 주석 텍스트다.
고치려면 디바이스 회전을 풀고 벌크 배선을 다시 짜야 하는데, 그건 검증된 연결을
건드리는 일이라 지금 하지 않았다.

## 5. 하지 않은 것

- **xschem은 로컬에 없지만 이제 돈다** — §4.1의 자체 이미지로. 남은 것은
  GUI 편집이다: 이 저장소는 schematic을 텍스트로 쓰고 툴로 넷리스트하지,
  화면에서 그리지 않는다(헤드리스 환경이라 그렇다). `.sch`는 사람이 편집하는
  소스 포맷이므로 그 자체는 정상적인 사용 방식이다.
  magic과 klayout은 다르다 — 로컬에는 없지만 이미 쓰고 있는 OpenLane
  이미지 안에 있고, 실측으로 확인했다: magic이 마운트된 PDK의 진짜
  `sky130A.tech`를 0.24 s에 로드하고(`tech name` → `sky130A`), klayout
  0.29.4가 1.6 s에 응답한다. 그래서 `evaluate()`는 로컬에 없으면 컨테이너로
  떨어진다 — `status --docker`가 available이라고 한 백엔드를 `evaluate()`가
  not_installed로 거절하던 불일치를 메운 것이다.
- **schematic → layout → LVS 루프는 아직 닫히지 않았다.** netgen은 실행되지만
  (Tcl 백엔드 실측 확인) 아날로그 셀의 schematic 넷리스트와 layout 넷리스트를
  비교하는 경로는 없다. 그러려면 §5의 첫 항목이 먼저 풀려야 한다.
- **PEX(기생 추출)가 없다.** Quantus/xRC 대응이 비어 있고, 지금 지연값은
  `Cl 10f`라는 손으로 넣은 부하 기준이다. 추출된 기생이 아니다.
- **아날로그 결과는 아직 reference-db에 들어가지 않는다.** 케이스 스키마는
  디지털 후보(candidate/verdict/signoff 23개 체크) 모양이고, 아날로그 측정을
  거기에 억지로 끼워 넣으면 `surrogate.py`와 `pareto.py`가 읽는 라벨이
  오염된다. 아날로그 케이스 스키마는 별도 결정이 필요하다.
- **RL·서로게이트 확장 없음.** 아날로그 샘플은 현재 코너 3개 × 셀 1개다.

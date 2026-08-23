import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Lang = "en" | "ko";

const LANG_STORAGE_KEY = "ppa-eda-agent-dashboard:lang";

const dict = {
  eyebrow: { en: "DTCO AI Agent", ko: "DTCO AI 에이전트" },
  title: { en: "DTCO Agent Console", ko: "DTCO 에이전트 콘솔" },
  subtitle: {
    en: "A design-technology co-optimization agent that runs real OpenLane2 placement/routing candidates and repairs them itself — this page is its control surface, not a static report. Trigger a real run, watch the agent work, or fall back to reading pasted reports and a live OpenSTA simulation.",
    ko: "실제 OpenLane2 배치/배선 후보를 생성하고 스스로 문제를 고쳐나가는 DTCO(설계-공정 공동 최적화) 에이전트입니다 — 이 화면은 정적인 리포트가 아니라 그 에이전트를 직접 조작하는 콘솔입니다. 실제 실행을 트리거해 에이전트가 일하는 과정을 지켜보거나, 붙여넣은 리포트나 실시간 OpenSTA 시뮬레이션을 확인할 수도 있습니다.",
  },
  tab_simulate: { en: "Simulate", ko: "시뮬레이션" },
  tab_area: { en: "Area", ko: "Area" },
  tab_timing: { en: "Timing", ko: "Timing" },
  tab_power: { en: "Power", ko: "Power" },
  tab_tradeoffs: { en: "Trade-offs", ko: "트레이드오프" },
  tab_pipeline: { en: "Layout Pipeline", ko: "레이아웃 파이프라인" },
  // Names what these tabs *are for* rather than what they contain. They
  // are a separate capability from the pipeline above (paste an existing
  // EDA report, or run a one-off OpenSTA sim), and a bare "reports"
  // label left newcomers reading them as more pipeline output.
  nav_reports_label: { en: "analyze your own reports", ko: "직접 가져온 리포트 분석" },

  pipeline_panel_title: {
    en: "autonomous layout pipeline — real OpenLane2 runs from reference-db/",
    ko: "자율형 레이아웃 파이프라인 — reference-db/의 실제 OpenLane2 실행 결과",
  },
  pipeline_intro: {
    en: "Every case below is a real pipeline.orchestrator.py run: real placement/routing candidates, real OpenLane metrics.json verdicts, real failures where they occurred. See docs/superpowers/specs/2026-08-21-autonomous-layout-agent-design.md for the full design.",
    ko: "아래 각 케이스는 실제 pipeline.orchestrator.py 실행 결과입니다: 실제 배치/배선 후보, 실제 OpenLane metrics.json 판정, 발생한 실제 실패까지 그대로 보여줍니다. 전체 설계는 docs/superpowers/specs/2026-08-21-autonomous-layout-agent-design.md 참고.",
  },
  pipeline_loading: { en: "Loading reference-db…", ko: "reference-db 불러오는 중…" },
  pipeline_error_hint: {
    en: "is the simulation server (node server/index.mjs) running?",
    ko: "시뮬레이션 서버(node server/index.mjs)가 실행 중인가요?",
  },
  pipeline_empty: {
    en: "No cases yet — run pipeline/orchestrator.py on a design to populate reference-db/.",
    ko: "아직 케이스가 없습니다 — pipeline/orchestrator.py를 실행해 reference-db/를 채워보세요.",
  },
  pipeline_filter_design: { en: "design", ko: "디자인" },
  pipeline_filter_all: { en: "all designs", ko: "전체" },
  pipeline_run_button: { en: "run agent now", ko: "지금 에이전트 실행" },
  pipeline_run_running: { en: "agent running…", ko: "에이전트 실행 중…" },
  pipeline_run_done: { en: "run finished — case updated below", ko: "실행 완료 — 아래 케이스가 갱신되었습니다" },
  pipeline_run_failed: { en: "run failed", ko: "실행 실패" },
  pipeline_translate_button: { en: "translate (machine)", ko: "번역보기 (기계번역)" },
  pipeline_translate_loading: { en: "translating…", ko: "번역 중…" },
  pipeline_translate_label: { en: "machine translation — original above is authoritative", ko: "기계번역 — 원문(위)이 정확한 기준입니다" },
  pipeline_translate_needs_key: { en: "needs a hermes-gateway key (see Diagnosis tab)", ko: "hermes-gateway 키가 필요합니다 (진단 탭 참고)" },
  hiw_title: { en: "how this works — read this first", ko: "이 서비스는 이렇게 동작합니다 — 먼저 읽어보세요" },
  hiw_lede: {
    en: "A DTCO agent that closes chip layout by itself. You give it RTL and PPA targets; it proposes physical-design configurations, runs each one through the real OpenLane2 flow on the sky130 PDK, and judges the result against the targets. When a run fails in a way it recognises, it fixes the configuration and tries again — unaided. When it doesn't recognise the failure, it stops and asks a human instead of guessing.",
    ko: "칩 레이아웃을 스스로 완성하는 DTCO 에이전트입니다. RTL과 PPA 목표를 주면, 물리 설계 설정을 제안하고, 각각을 sky130 PDK 위에서 실제 OpenLane2 플로우로 돌린 뒤, 결과를 목표와 대조해 판정합니다. 아는 유형으로 실패하면 설정을 스스로 고쳐 다시 시도하고, 모르는 실패면 추측하지 않고 멈춰서 사람에게 묻습니다.",
  },
  hiw_step_input: {
    en: "RTL + PPA targets (utilization, timing, DRC/LVS clean)",
    ko: "RTL + PPA 목표 (밀도, 타이밍, DRC/LVS 무결)",
  },
  hiw_step_propose: {
    en: "Agent proposes candidate configurations (utilization, die size, synthesis strategy)",
    ko: "에이전트가 후보 설정을 제안 (밀도, 다이 크기, 합성 전략)",
  },
  hiw_step_run: {
    en: "Each candidate runs the real OpenLane2 flow — synthesis, floorplan, place, CTS, route, signoff",
    ko: "각 후보를 실제 OpenLane2 플로우로 실행 — 합성, 플로어플랜, 배치, CTS, 배선, 사인오프",
  },
  hiw_step_score: {
    en: "Judged on OpenLane's own metrics.json — never an estimate",
    ko: "OpenLane이 직접 낸 metrics.json으로 판정 — 추정값이 아님",
  },
  hiw_outcome_pass: {
    en: "A candidate met every target. Among several, the winner is chosen by multi-objective Pareto ranking (area / power / timing margin together), not by one metric.",
    ko: "목표를 모두 만족한 후보. 여러 개면 단일 지표가 아니라 다목적 파레토 랭킹(면적·전력·타이밍 마진을 함께)으로 승자를 정합니다.",
  },
  hiw_outcome_repair: {
    en: "Failed in a way the agent has really seen before (e.g. power-grid straps don't fit at this utilization). It edits the configuration and re-runs, up to a bounded iteration budget.",
    ko: "에이전트가 실제로 겪어본 유형의 실패 (예: 이 밀도에서는 전원 그리드 스트랩이 안 들어감). 설정을 고쳐 재실행하며, 반복 횟수에는 상한이 있습니다.",
  },
  hiw_outcome_escalate: {
    en: "Failed in a way no known repair pattern covers. The agent stops and files a review request for a specialist subagent or a human — deliberately, rather than guessing at a fix.",
    ko: "알려진 복구 패턴에 없는 실패. 에이전트는 멈추고 전문 서브에이전트나 사람에게 리뷰를 요청합니다 — 추측으로 고치지 않기 위한 의도된 동작입니다.",
  },
  hiw_real: {
    en: "Everything below is real. Every number came from an actual OpenLane run in Docker against a real PDK — including the failures, which are kept rather than hidden. Nothing here is simulated or estimated.",
    ko: "아래 내용은 전부 실제입니다. 모든 수치는 Docker에서 실제 PDK로 돌린 진짜 OpenLane 실행에서 나온 값이며, 실패한 결과도 숨기지 않고 그대로 남겨둡니다. 시뮬레이션이나 추정치는 하나도 없습니다.",
  },
  hiw_stat_designs: { en: "designs", ko: "설계" },
  hiw_stat_cases: { en: "cases", ko: "케이스" },
  hiw_stat_runs: { en: "real candidate runs", ko: "실제 후보 실행" },
  hiw_stat_closed: { en: "closed by the agent", ko: "에이전트가 완료" },
  hiw_stat_repaired: { en: "self-repaired candidates", ko: "자가복구된 후보" },

  hitl_needs_you: { en: "NEEDS YOU", ko: "개입 필요" },
  live_open_cases: {
    en: "{n} case(s) waiting on a human decision",
    ko: "사람의 판단을 기다리는 케이스 {n}개",
  },
  live_all_closed: {
    en: "every case closed — nothing waiting on you",
    ko: "모든 케이스 완료 — 대기 중인 항목 없음",
  },
  live_refreshed: { en: "live · refreshed {t}", ko: "실시간 · {t} 갱신" },

  phase_understand: { en: "UNDERSTAND", ko: "파악" },
  phase_understand_role: {
    en: "read the design — no candidate exists yet",
    ko: "설계를 읽는 단계 — 아직 후보가 없음",
  },
  phase_propose: { en: "PROPOSE", ko: "제안" },
  phase_propose_role: {
    en: "turn that reading into concrete configurations to try",
    ko: "파악한 내용을 실제로 시도할 설정 후보로 변환",
  },
  phase_evaluate: { en: "EVALUATE", ko: "평가" },
  phase_evaluate_role: {
    en: "gates each candidate passes through in a real OpenLane run — a candidate is tagged with the last gate it reached",
    ko: "각 후보가 실제 OpenLane 실행에서 통과해야 하는 관문 — 후보에는 도달한 마지막 관문이 표시됨",
  },
  phase_decide: { en: "DECIDE", ko: "판단" },
  phase_decide_role: {
    en: "winner, repair and retry, or escalate to a human",
    ko: "승자 확정 · 복구 후 재시도 · 사람에게 에스컬레이션 중 하나",
  },
  phase_loop_fired: {
    en: "repair loop — {n} candidate(s) here came from DECIDE feeding a new configuration back into PROPOSE",
    ko: "복구 루프 — 이 케이스의 후보 {n}개는 '판단'이 새 설정을 '제안'으로 되돌려 만들어졌습니다",
  },
  phase_loop_idle: {
    en: "repair loop — not needed in this case; DECIDE can send a new configuration back to PROPOSE when a run fails a way it recognises",
    ko: "복구 루프 — 이 케이스에선 불필요했습니다. 아는 유형으로 실패하면 '판단'이 새 설정을 '제안'으로 되돌립니다",
  },

  review_step_request: { en: "1. generate review request", ko: "1. 리뷰 요청 생성" },
  review_step_ask: { en: "2. get an AI review", ko: "2. AI 리뷰 받기" },
  review_step_apply: { en: "3. apply into the case", ko: "3. 케이스에 반영" },
  review_asking: { en: "reviewing…", ko: "리뷰 중…" },
  review_show_request: { en: "show request", ko: "요청 내용 보기" },
  review_hide_request: { en: "hide request", ko: "요청 내용 숨기기" },
  review_result_label: {
    en: "AI review — recorded as hermes-review when applied, not as a subagent",
    ko: "AI 리뷰 — 반영 시 서브에이전트가 아니라 hermes-review 이름으로 기록됩니다",
  },
  pipeline_case_candidates: { en: "candidates", ko: "후보" },
  pipeline_layout_expand: { en: "(click to enlarge)", ko: "(클릭하면 확대)" },
  pipeline_layout_collapse: { en: "(click to shrink)", ko: "(클릭하면 축소)" },
  pipeline_layout_image_label: {
    en: "rendered layout — real GDS via KLayout",
    ko: "렌더링된 레이아웃 — KLayout으로 생성한 실제 GDS",
  },
  pipeline_agent_legend_title: { en: "which agent does what — 8 subagents", ko: "어떤 에이전트가 무엇을 하는지 — 8개 서브에이전트" },
  pipeline_agent_legend_diagnosis_note: {
    en: "Separate from the 8-stage pipeline — the report-paste / live-simulation diagnosis agent behind the sidebar's own tab.",
    ko: "8단계 파이프라인과는 별개 — 사이드바의 진단 탭에서 리포트 붙여넣기/실시간 시뮬레이션 진단을 담당하는 에이전트입니다.",
  },
  pipeline_translate_long_wait_hint: {
    en: "long diagnosis text can take a few minutes — this gateway model delivers the full translation at once, not token-by-token, so nothing appears until it's done",
    ko: "긴 진단문은 몇 분 걸릴 수 있습니다 — 이 게이트웨이 모델은 토큰 단위가 아니라 전체 번역을 한번에 전달하므로, 끝날 때까지는 아무것도 표시되지 않습니다",
  },

  load_example: { en: "Load example", ko: "예시 불러오기" },

  sim_panel_title: {
    en: "live OpenSTA simulation — 5-cell design, real Nangate45 library",
    ko: "실시간 OpenSTA 시뮬레이션 — 5셀 설계, 실제 Nangate45 라이브러리",
  },
  sim_clock_period: { en: "Clock period (ns)", ko: "클록 주기 (ns)" },
  sim_run: { en: "Run simulation", ko: "시뮬레이션 실행" },
  sim_running: { en: "Running OpenSTA…", ko: "OpenSTA 실행 중…" },
  sim_hint: {
    en: "Tightening the period below ~0.13ns will produce a real timing violation — try it.",
    ko: "주기를 ~0.13ns 아래로 줄이면 실제 타이밍 위반이 발생합니다 — 시도해보세요.",
  },
  sim_error_hint: {
    en: "Is the simulation server running?",
    ko: "시뮬레이션 서버가 실행 중인가요?",
  },
  sim_raw_output: { en: "raw OpenSTA output", ko: "OpenSTA 원본 출력" },
  sim_parsed_timing: { en: "parsed timing", ko: "파싱된 타이밍" },
  sim_parsed_power: { en: "parsed power", ko: "파싱된 전력" },
  sim_diagnosis_title: {
    en: "ppa-eda-analyst — live diagnosis",
    ko: "ppa-eda-analyst — 실시간 진단",
  },
  sim_diagnose_button: { en: "Diagnose this result", ko: "이 결과 진단하기" },
  sim_diagnosing: {
    en: "ppa-eda-analyst is thinking…",
    ko: "ppa-eda-analyst가 분석 중…",
  },

  key_metrics: { en: "key metrics", ko: "핵심 지표" },
  total_cell_area: { en: "Total cell area", ko: "전체 셀 면적" },
  combinational: { en: "Combinational", ko: "조합" },
  noncombinational: { en: "Noncombinational", ko: "비조합" },
  macro_area: { en: "Macro/black box", ko: "매크로/블랙박스" },
  bufinv_subset: {
    en: "buf/inv (subset of combinational)",
    ko: "buf/inv (조합 영역 일부)",
  },
  number_of_cells: { en: "Number of cells", ko: "셀 개수" },
  area_breakdown: { en: "area breakdown (µm²)", ko: "면적 분포 (µm²)" },
  area_floorplan: {
    en: "area floorplan — block size ∝ actual area",
    ko: "면적 플로어플랜 — 블록 크기 ∝ 실제 면적",
  },
  area_floorplan_caption: {
    en: "Each block's size is proportional to its share of total cell area — the closest thing to seeing the die's real footprint from a report_area dump.",
    ko: "각 블록의 크기는 전체 셀 면적에서 차지하는 비율에 비례합니다 — report_area 텍스트만으로 다이의 실제 면적을 가장 직관적으로 보는 방법입니다.",
  },

  wns: { en: "WNS", ko: "WNS" },
  violated_paths: { en: "Violated paths", ko: "위반 경로" },
  slack_per_path: { en: "slack per path (ns)", ko: "경로별 슬랙 (ns)" },
  timing_summary: { en: "timing summary", ko: "타이밍 요약" },

  total_power: { en: "Total power", ko: "총 전력" },
  total_dynamic_power: { en: "Total dynamic power", ko: "총 동적 전력" },
  cell_internal_power: { en: "Cell internal power", ko: "셀 내부 전력" },
  net_switching_power: { en: "Net switching power", ko: "넷 스위칭 전력" },
  cell_leakage_power: { en: "Cell leakage power", ko: "셀 누설 전력" },
  power_breakdown: { en: "power breakdown", ko: "전력 분포" },

  ppa_triangle_title: { en: "the PPA triangle", ko: "PPA 삼각형" },
  ppa_triangle_caption: {
    en: "Every optimization pulls this triangle — rarely can you improve one corner without giving up another.",
    ko: "모든 최적화는 이 삼각형을 잡아당깁니다 — 한쪽을 개선하면서 다른 쪽을 희생하지 않는 경우는 드뭅니다.",
  },
  tradeoffs_chart_title: {
    en: "how common techniques trade PPA",
    ko: "흔한 기법들이 PPA를 어떻게 주고받는가",
  },
  legend_improves: { en: "improves / saves", ko: "개선 / 절약" },
  legend_worsens: { en: "costs more / worsens", ko: "악화 / 비용 증가" },
  legend_neutral: { en: "no meaningful change", ko: "유의미한 변화 없음" },
  why_each_case: { en: "why, in each case", ko: "각각 왜 그런가" },

  server_key_configured: {
    en: "Server has its own hermes-gateway key configured (PPA_EDA_GATEWAY_KEY) — no key needed here.",
    ko: "서버가 자체 hermes-gateway 키를 갖고 있습니다 (PPA_EDA_GATEWAY_KEY) — 여기서 키를 입력할 필요 없습니다.",
  },
  key_input_prompt: {
    en: "Enter your hermes-gateway client key to get a live diagnosis.",
    ko: "실시간 진단을 받으려면 hermes-gateway 클라이언트 키를 입력하세요.",
  },
  key_input_hint: {
    en: "Stored only in this browser's localStorage.",
    ko: "이 브라우저의 localStorage에만 저장됩니다.",
  },
  save_key: { en: "Save key", ko: "키 저장" },
  clear_key: { en: "Clear key", ko: "키 지우기" },

  couldnt_parse_area: {
    en: "Couldn't parse this as an area report",
    ko: "area 리포트로 파싱하지 못했습니다",
  },

  agent_sidebar_title: { en: "ppa-eda-analyst", ko: "ppa-eda-analyst" },
  agent_sidebar_subtitle: {
    en: "hermes profile · model minimax/minimax-m3 · via hermes-gateway :8700",
    ko: "hermes 프로필 · 모델 minimax/minimax-m3 · hermes-gateway :8700 경유",
  },
  agent_checklist_title: { en: "diagnostic checklist", ko: "진단 체크리스트" },
  agent_checklist_1: {
    en: "Identify report type",
    ko: "리포트 종류 식별",
  },
  agent_checklist_2: {
    en: "Extract key metrics (WNS, power split, area split)",
    ko: "핵심 지표 추출 (WNS, 전력/면적 분포)",
  },
  agent_checklist_3: {
    en: "Flag violations and anomalies",
    ko: "위반·이상 징후 표시",
  },
  agent_checklist_4: {
    en: "Map violations to likely root causes",
    ko: "위반을 근본 원인으로 매핑",
  },
  agent_checklist_5: {
    en: "Propose PPA-tradeoff-labeled fixes",
    ko: "PPA 트레이드오프를 명시한 해결책 제안",
  },
  agent_idle: {
    en: "Run a simulation in the Simulate tab, then click \"Diagnose this result\" to see this agent work live, right here.",
    ko: "Simulate 탭에서 시뮬레이션을 돌린 뒤 \"Diagnose this result\"를 누르면 이 에이전트가 실시간으로 작동하는 걸 여기서 볼 수 있습니다.",
  },
  agent_streaming_live: { en: "● streaming live", ko: "● 실시간 스트리밍 중" },
  agent_tokens: { en: "chunks", ko: "청크" },
  agent_confirmed_upstream: {
    en: "Confirmed live response from upstream:",
    ko: "실제 upstream으로부터 온 응답 확인됨:",
  },
} as const;

export type DictKey = keyof typeof dict;

const LangContext = createContext<{
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: DictKey) => string;
}>({
  lang: "en",
  setLang: () => {},
  t: (key) => dict[key].en,
});

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(
    () => (localStorage.getItem(LANG_STORAGE_KEY) as Lang | null) ?? "en"
  );

  useEffect(() => {
    localStorage.setItem(LANG_STORAGE_KEY, lang);
  }, [lang]);

  const t = (key: DictKey) => dict[key][lang];

  return (
    <LangContext.Provider value={{ lang, setLang, t }}>
      {children}
    </LangContext.Provider>
  );
}

export function useLang() {
  return useContext(LangContext);
}

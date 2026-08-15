import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Lang = "en" | "ko";

const LANG_STORAGE_KEY = "ppa-eda-agent-dashboard:lang";

const dict = {
  eyebrow: { en: "Synopsys report reader", ko: "Synopsys 리포트 리더" },
  title: { en: "PPA Readout", ko: "PPA Readout" },
  subtitle: {
    en: "Run a real OpenSTA simulation, paste a report_area / report_timing / report_power dump, or see how common fixes trade Power, Performance, and Area against each other.",
    ko: "실제 OpenSTA 시뮬레이션을 돌리거나, report_area / report_timing / report_power 리포트를 붙여넣거나, 흔한 최적화 기법들이 Power/Performance/Area를 어떻게 주고받는지 확인하세요.",
  },
  tab_simulate: { en: "Simulate", ko: "시뮬레이션" },
  tab_area: { en: "Area", ko: "Area" },
  tab_timing: { en: "Timing", ko: "Timing" },
  tab_power: { en: "Power", ko: "Power" },
  tab_tradeoffs: { en: "Trade-offs", ko: "트레이드오프" },

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

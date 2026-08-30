import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { LangProvider, useLang } from "./i18n";
import { NAV_ICONS } from "./components/NavIcons";
import { AgentProvider, useAgent } from "./agentContext";
import "./App.css";

const AreaTab = lazy(() => import("./components/AreaTab"));
const TimingTab = lazy(() => import("./components/TimingTab"));
const PowerTab = lazy(() => import("./components/PowerTab"));
const TradeoffsTab = lazy(() => import("./components/TradeoffsTab"));
const SimulateTab = lazy(() => import("./components/SimulateTab"));
const DiagnosisPage = lazy(() => import("./components/DiagnosisPage"));
const PipelineTab = lazy(() => import("./components/PipelineTab"));
const SystemHealth = lazy(() => import("./components/SystemHealth"));
const ProgressTab = lazy(() => import("./components/ProgressTab"));
const AskPage = lazy(() => import("./components/AskPage"));
const DataLineage = lazy(() => import("./components/DataLineage"));
const ManualPage = lazy(() => import("./components/ManualPage"));

type TabId =
  | "pipeline"
  | "health"
  | "progress"
  | "ask"
  | "lineage"
  | "manual"
  | "simulate"
  | "area"
  | "timing"
  | "power"
  | "tradeoffs"
  | "diagnosis";
type Theme = "dark" | "light";

const THEME_STORAGE_KEY = "ppa-eda-agent-dashboard:theme";
// Remembered like the theme: the sidebar is chrome, and a reader who
// collapsed it wants it collapsed on the next load rather than every
// visit starting with a decision they already made.
const COLLAPSED_STORAGE_KEY = "ppa-eda-agent-dashboard:sidebar-collapsed";

// One sidebar destination. Every button in this nav was written out by
// hand with the same three-line className ternary, which is how the
// icons were going to be added in twelve places and forgotten in one.
//
// `title` carries the label for the collapsed rail, where the icon is
// all that is visible. aria-label carries it unconditionally, so the
// accessible name does not disappear along with the text.
function NavItem({
  id,
  label,
  active,
  onSelect,
  variant,
  badge,
}: {
  id: TabId;
  label: string;
  active: boolean;
  onSelect: (id: TabId) => void;
  variant?: "primary" | "agent";
  badge?: ReactNode;
}) {
  const classes = ["app__nav-item"];
  if (variant === "primary") classes.push("app__nav-item--primary");
  if (variant === "agent") classes.push("app__nav-item--agent");
  if (active) classes.push("app__nav-item--active");
  return (
    <button
      className={classes.join(" ")}
      onClick={() => onSelect(id)}
      title={label}
      aria-label={label}
      aria-current={active ? "page" : undefined}
    >
      <span className="app__nav-icon">{NAV_ICONS[id]}</span>
      <span className="app__nav-text">{label}</span>
      {badge}
    </button>
  );
}

function AppInner() {
  const { lang, setLang, t } = useLang();
  const { diagnosing, hasUnseenResult } = useAgent();
  // Pipeline is the primary surface of this app (real placement/routing
  // candidate generation + evaluation) — everything else (report-paste
  // tabs, live sim) is secondary, so it's the default view and sits
  // first/highlighted in the sidebar rather than buried among report tabs.
  const [active, setActive] = useState<TabId>("pipeline");
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(THEME_STORAGE_KEY) as Theme | null) ?? "dark"
  );
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1"
  );

  useEffect(() => {
    localStorage.setItem(COLLAPSED_STORAGE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  const PRIMARY_TAB: { id: TabId; label: string } = { id: "pipeline", label: t("tab_pipeline") };
  // A peer of the pipeline, not one of the report tabs: both are about
  // the agent system itself, where the report tabs analyse material the
  // user brings in. It was previously a collapsed block inside the
  // pipeline page, which put system-wide state in among per-case cards.
  const HEALTH_TAB: { id: TabId; label: string } = { id: "health", label: t("tab_health") };
  // Sits with the other two rather than in the reports group: all three
  // are about operating this system, where the report tabs analyse
  // material the user brings in.
  // Beside health rather than inside it: health asks whether anything
  // needs attention, this asks what the store holds and where it goes.
  // Different questions, and the second had no page at all.
  // Beside health and lineage, not in the reports group: those three
  // and this are all about the agent system itself. Health asks what
  // needs attention now and lineage asks what the store holds; this
  // asks whether any of it is getting better, which neither answered.
  const PROGRESS_TAB: { id: TabId; label: string } = { id: "progress", label: t("tab_progress") };
  const LINEAGE_TAB: { id: TabId; label: string } = { id: "lineage", label: t("tab_lineage") };
  // Next to the manual rather than among the report tabs: both answer
  // "how does this work", one by being read and one by being asked.
  const ASK_TAB: { id: TabId; label: string } = { id: "ask", label: t("tab_ask") };
  const MANUAL_TAB: { id: TabId; label: string } = { id: "manual", label: t("tab_manual") };
  const REPORT_TABS: { id: TabId; label: string }[] = [
    { id: "simulate", label: t("tab_simulate") },
    { id: "area", label: t("tab_area") },
    { id: "timing", label: t("tab_timing") },
    { id: "power", label: t("tab_power") },
    { id: "tradeoffs", label: t("tab_tradeoffs") },
  ];

  return (
    <div className="app app--sidebar">
      <aside className={collapsed ? "app__sidebar app__sidebar--collapsed" : "app__sidebar"}>
        <button
          className="app__collapse"
          onClick={() => setCollapsed((v) => !v)}
          title={t(collapsed ? "sidebar_expand" : "sidebar_collapse")}
          aria-label={t(collapsed ? "sidebar_expand" : "sidebar_collapse")}
          aria-expanded={!collapsed}
        >
          {NAV_ICONS[collapsed ? "expand" : "collapse"]}
        </button>
        <div className="app__brand">
          <div className="app__mark" aria-hidden="true">
            <span>P</span><span>P</span><span>A</span>
          </div>
          <div>
            <span className="app__eyebrow">{t("eyebrow")}</span>
            <h1>{t("title")}</h1>
          </div>
        </div>

        <nav className="app__nav">
          {[PRIMARY_TAB, HEALTH_TAB, PROGRESS_TAB, LINEAGE_TAB, ASK_TAB, MANUAL_TAB]
            .map((tab) => (
              <NavItem
                key={tab.id}
                id={tab.id}
                label={tab.label}
                active={active === tab.id}
                onSelect={setActive}
                variant="primary"
              />
            ))}

          {/* Hidden when collapsed rather than shortened: a section
              heading squeezed into a 3rem rail is a smudge, and the
              group it names is still legible from the icons. */}
          <span className="app__nav-label">{t("nav_reports_label")}</span>
          {REPORT_TABS.map((tab) => (
            <NavItem
              key={tab.id}
              id={tab.id}
              label={tab.label}
              active={active === tab.id}
              onSelect={setActive}
            />
          ))}

          <NavItem
            id="diagnosis"
            label={t("agent_sidebar_title")}
            active={active === "diagnosis"}
            onSelect={setActive}
            variant="agent"
            badge={
              diagnosing ? (
                <span className="app__tab-dot app__tab-dot--live" />
              ) : hasUnseenResult ? (
                <span className="app__tab-dot app__tab-dot--unseen" />
              ) : null
            }
          />
        </nav>

        <div className="app__sidebar-footer">
          <span className="app__system-status"><i /> OpenLane connected</span>
          <div className="app__sidebar-controls">
            <button
              className="app__theme-toggle"
              onClick={() => setLang(lang === "en" ? "ko" : "en")}
            >
              {lang === "en" ? "한국어" : "English"}
            </button>
            <button
              className="app__theme-toggle"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? "☀ Light" : "● Dark"}
            </button>
          </div>
        </div>
      </aside>

      <div className="app__content">
        <header className="app__topbar">
          <p>{t("subtitle")}</p>
          <div className="app__signal-line" aria-hidden="true">
            {Array.from({ length: 28 }, (_, i) => <span key={i} />)}
          </div>
        </header>
        <main className="app__main">
          <Suspense fallback={<div className="panel"><span className="panel__title">Loading…</span></div>}>
            {active === "simulate" && <SimulateTab />}
            {active === "area" && <AreaTab />}
            {active === "timing" && <TimingTab />}
            {active === "power" && <PowerTab />}
            {active === "tradeoffs" && <TradeoffsTab />}
            {active === "pipeline" && <PipelineTab />}
            {active === "health" && <SystemHealth standalone />}
            {active === "progress" && <ProgressTab />}
            {active === "lineage" && <DataLineage />}
            {active === "ask" && <AskPage />}
            {active === "manual" && <ManualPage />}
            {active === "diagnosis" && <DiagnosisPage />}
          </Suspense>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <LangProvider>
      <AgentProvider>
        <AppInner />
      </AgentProvider>
    </LangProvider>
  );
}

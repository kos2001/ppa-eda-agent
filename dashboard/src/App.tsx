import { lazy, Suspense, useEffect, useState } from "react";
import { LangProvider, useLang } from "./i18n";
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
const ManualPage = lazy(() => import("./components/ManualPage"));

type TabId =
  | "pipeline"
  | "health"
  | "manual"
  | "simulate"
  | "area"
  | "timing"
  | "power"
  | "tradeoffs"
  | "diagnosis";
type Theme = "dark" | "light";

const THEME_STORAGE_KEY = "ppa-eda-agent-dashboard:theme";

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
      <aside className="app__sidebar">
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
          <button
            className={
              active === PRIMARY_TAB.id
                ? "app__nav-item app__nav-item--primary app__nav-item--active"
                : "app__nav-item app__nav-item--primary"
            }
            onClick={() => setActive(PRIMARY_TAB.id)}
          >
            {PRIMARY_TAB.label}
          </button>

          <button
            className={
              active === HEALTH_TAB.id
                ? "app__nav-item app__nav-item--primary app__nav-item--active"
                : "app__nav-item app__nav-item--primary"
            }
            onClick={() => setActive(HEALTH_TAB.id)}
          >
            {HEALTH_TAB.label}
          </button>

          <button
            className={
              active === MANUAL_TAB.id
                ? "app__nav-item app__nav-item--primary app__nav-item--active"
                : "app__nav-item app__nav-item--primary"
            }
            onClick={() => setActive(MANUAL_TAB.id)}
          >
            {MANUAL_TAB.label}
          </button>

          <span className="app__nav-label">{t("nav_reports_label")}</span>
          {REPORT_TABS.map((tab) => (
            <button
              key={tab.id}
              className={
                active === tab.id ? "app__nav-item app__nav-item--active" : "app__nav-item"
              }
              onClick={() => setActive(tab.id)}
            >
              {tab.label}
            </button>
          ))}

          <button
            className={
              active === "diagnosis"
                ? "app__nav-item app__nav-item--active app__nav-item--agent"
                : "app__nav-item app__nav-item--agent"
            }
            onClick={() => setActive("diagnosis")}
          >
            {t("agent_sidebar_title")}
            {diagnosing && <span className="app__tab-dot app__tab-dot--live" />}
            {!diagnosing && hasUnseenResult && (
              <span className="app__tab-dot app__tab-dot--unseen" />
            )}
          </button>
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

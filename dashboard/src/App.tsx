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

type TabId =
  | "area"
  | "timing"
  | "power"
  | "tradeoffs"
  | "simulate"
  | "pipeline"
  | "diagnosis";
type Theme = "dark" | "light";

const THEME_STORAGE_KEY = "ppa-eda-agent-dashboard:theme";

function AppInner() {
  const { lang, setLang, t } = useLang();
  const { diagnosing, hasUnseenResult } = useAgent();
  const [active, setActive] = useState<TabId>("simulate");
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(THEME_STORAGE_KEY) as Theme | null) ?? "dark"
  );

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  const TABS: { id: TabId; label: string }[] = [
    { id: "simulate", label: t("tab_simulate") },
    { id: "area", label: t("tab_area") },
    { id: "timing", label: t("tab_timing") },
    { id: "power", label: t("tab_power") },
    { id: "tradeoffs", label: t("tab_tradeoffs") },
    { id: "pipeline", label: t("tab_pipeline") },
  ];

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__header-row">
          <div>
            <span className="app__eyebrow">{t("eyebrow")}</span>
            <h1>{t("title")}</h1>
          </div>
          <div className="app__header-controls">
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
        <p>{t("subtitle")}</p>
      </header>
      <nav className="app__tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={
              active === tab.id ? "app__tab app__tab--active" : "app__tab"
            }
            onClick={() => setActive(tab.id)}
          >
            {tab.label}
          </button>
        ))}
        <button
          className={
            active === "diagnosis"
              ? "app__tab app__tab--active app__tab--agent"
              : "app__tab app__tab--agent"
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
      <main className="app__main">
        <Suspense fallback={<div className="panel"><span className="panel__title">Loading…</span></div>}>
          {active === "simulate" && <SimulateTab />}
          {active === "area" && <AreaTab />}
          {active === "timing" && <TimingTab />}
          {active === "power" && <PowerTab />}
          {active === "tradeoffs" && <TradeoffsTab />}
          {active === "pipeline" && <PipelineTab />}
          {active === "diagnosis" && <DiagnosisPage />}
        </Suspense>
      </main>
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

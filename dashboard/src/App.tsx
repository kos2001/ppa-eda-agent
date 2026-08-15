import { useEffect, useState } from "react";
import AreaTab from "./components/AreaTab";
import TimingTab from "./components/TimingTab";
import PowerTab from "./components/PowerTab";
import TradeoffsTab from "./components/TradeoffsTab";
import SimulateTab from "./components/SimulateTab";
import AgentSidebar from "./components/AgentSidebar";
import { LangProvider, useLang } from "./i18n";
import { AgentProvider } from "./agentContext";
import "./App.css";

type TabId = "area" | "timing" | "power" | "tradeoffs" | "simulate";
type Theme = "dark" | "light";

const THEME_STORAGE_KEY = "ppa-eda-agent-dashboard:theme";

function AppInner() {
  const { lang, setLang, t } = useLang();
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
      </nav>
      <div className="app__body">
        <main className="app__main">
          {active === "simulate" && <SimulateTab />}
          {active === "area" && <AreaTab />}
          {active === "timing" && <TimingTab />}
          {active === "power" && <PowerTab />}
          {active === "tradeoffs" && <TradeoffsTab />}
        </main>
        <AgentSidebar />
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

import { useState } from "react";
import AreaTab from "./components/AreaTab";
import TimingTab from "./components/TimingTab";
import PowerTab from "./components/PowerTab";
import TradeoffsTab from "./components/TradeoffsTab";
import SimulateTab from "./components/SimulateTab";
import "./App.css";

type TabId = "area" | "timing" | "power" | "tradeoffs" | "simulate";

const TABS: { id: TabId; label: string }[] = [
  { id: "simulate", label: "Simulate" },
  { id: "area", label: "Area" },
  { id: "timing", label: "Timing" },
  { id: "power", label: "Power" },
  { id: "tradeoffs", label: "Trade-offs" },
];

export default function App() {
  const [active, setActive] = useState<TabId>("simulate");

  return (
    <div className="app">
      <header className="app__header">
        <span className="app__eyebrow">Synopsys report reader</span>
        <h1>PPA Readout</h1>
        <p>Run a real OpenSTA simulation, paste a report_area / report_timing / report_power dump, or see how common fixes trade Power, Performance, and Area against each other.</p>
      </header>
      <nav className="app__tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={active === t.id ? "app__tab app__tab--active" : "app__tab"}
            onClick={() => setActive(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <main className="app__main">
        {active === "simulate" && <SimulateTab />}
        {active === "area" && <AreaTab />}
        {active === "timing" && <TimingTab />}
        {active === "power" && <PowerTab />}
        {active === "tradeoffs" && <TradeoffsTab />}
      </main>
    </div>
  );
}

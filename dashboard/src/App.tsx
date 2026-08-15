import { useState } from "react";
import AreaTab from "./components/AreaTab";
import TimingTab from "./components/TimingTab";
import PowerTab from "./components/PowerTab";
import "./App.css";

type TabId = "area" | "timing" | "power";

const TABS: { id: TabId; label: string }[] = [
  { id: "area", label: "Area" },
  { id: "timing", label: "Timing" },
  { id: "power", label: "Power" },
];

export default function App() {
  const [active, setActive] = useState<TabId>("area");

  return (
    <div className="app">
      <header className="app__header">
        <span className="app__eyebrow">Synopsys report reader</span>
        <h1>PPA Readout</h1>
        <p>Paste a report_area / report_timing / report_power dump and see the Power / Performance / Area numbers it contains.</p>
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
        {active === "area" && <AreaTab />}
        {active === "timing" && <TimingTab />}
        {active === "power" && <PowerTab />}
      </main>
    </div>
  );
}

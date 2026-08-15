import { useState } from "react";
import PipelinePanel from "./components/PipelinePanel";
import ChatPanel from "./components/ChatPanel";
import AreaTab from "./components/AreaTab";
import TimingTab from "./components/TimingTab";
import PowerTab from "./components/PowerTab";
import TradeoffsTab from "./components/TradeoffsTab";
import "./App.css";

type TabId =
  | "pipeline"
  | "chat"
  | "area"
  | "timing"
  | "power"
  | "tradeoffs";

const TAB_GROUPS: { group: string; tabs: { id: TabId; label: string }[] }[] = [
  {
    group: "Ansible PPA",
    tabs: [
      { id: "pipeline", label: "Pipeline" },
      { id: "chat", label: "Chat" },
    ],
  },
  {
    group: "Chip PPA",
    tabs: [
      { id: "area", label: "Area" },
      { id: "timing", label: "Timing" },
      { id: "power", label: "Power" },
      { id: "tradeoffs", label: "Trade-offs" },
    ],
  },
];

export default function App() {
  const [active, setActive] = useState<TabId>("pipeline");

  return (
    <div className="app">
      <header className="app__header">
        <span className="app__eyebrow">Two meanings of PPA, one dashboard</span>
        <h1>PPA Readout</h1>
        <p>
          Ansible <strong>P</strong>ersonal <strong>P</strong>ackage{" "}
          <strong>A</strong>rchive pipeline + hermes chat, alongside chip
          design <strong>P</strong>ower/<strong>P</strong>erformance/
          <strong>A</strong>rea report reading — same acronym, different
          worlds, merged into one place.
        </p>
      </header>
      <nav className="app__tabs">
        {TAB_GROUPS.map((g) => (
          <div className="app__tab-group" key={g.group}>
            <span className="app__tab-group-label">{g.group}</span>
            <div className="app__tab-group-buttons">
              {g.tabs.map((t) => (
                <button
                  key={t.id}
                  className={
                    active === t.id ? "app__tab app__tab--active" : "app__tab"
                  }
                  onClick={() => setActive(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </nav>
      <main className="app__main">
        {active === "pipeline" && <PipelinePanel />}
        {active === "chat" && <ChatPanel />}
        {active === "area" && <AreaTab />}
        {active === "timing" && <TimingTab />}
        {active === "power" && <PowerTab />}
        {active === "tradeoffs" && <TradeoffsTab />}
      </main>
    </div>
  );
}

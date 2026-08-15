# PPA EDA Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a React + Vite + TypeScript dashboard in `dashboard/` that parses pasted Synopsys `report_area`/`report_timing`/`report_power` text client-side and visualizes it with charts.

**Architecture:** Vite React SPA. Three tabs (Area/Timing/Power), each with a textarea + "Load example" button + chart. Parsers in `src/parsers/` are pure functions (text in, typed result or error out, never throw). No backend — everything runs in the browser.

**Tech Stack:** React 18, Vite, TypeScript, `recharts` for charting.

## Global Constraints

- Text-paste input only, no file upload (per spec "Scope").
- Parsers never throw on malformed input — always return a typed
  result-or-error, since parse failure on pasted text is expected (per
  spec section 3 "Error handling").
- Example data must be the literal snippets from
  `references/report-{area,timing,power}.md`, not invented data (per spec
  section 4).

---

### Task 1: Scaffold the Vite React TypeScript app

**Files:**
- Create: `dashboard/` (Vite-generated tree)

**Interfaces:**
- Produces: working `dashboard/` app with `npm run dev`/`npm run build`, `recharts` installed.

- [ ] **Step 1: Scaffold**

```bash
cd ~/gitspace/ppa-eda-agent
npm create vite@latest dashboard -- --template react-ts
cd dashboard
npm install
npm install recharts
```

- [ ] **Step 2: Verify it builds**

```bash
npm run build
```

Expected: succeeds, produces `dashboard/dist/`.

- [ ] **Step 3: Commit**

```bash
cd ~/gitspace/ppa-eda-agent
git add dashboard/
git commit -m "Scaffold PPA EDA dashboard: Vite + React + TypeScript + recharts"
```

---

### Task 2: Example report data and parsers

**Files:**
- Create: `dashboard/src/exampleReports.ts`
- Create: `dashboard/src/parsers/types.ts`
- Create: `dashboard/src/parsers/parseArea.ts`
- Create: `dashboard/src/parsers/parseTiming.ts`
- Create: `dashboard/src/parsers/parsePower.ts`
- Create: `dashboard/src/parsers/validate.mjs` (throwaway manual-check script, not shipped)

**Interfaces:**
- Produces: `EXAMPLE_AREA_REPORT`, `EXAMPLE_TIMING_REPORT`, `EXAMPLE_POWER_REPORT` string constants from `exampleReports.ts`; `AreaResult`, `TimingResult`, `PowerResult` types and `parseArea`, `parseTiming`, `parsePower` functions — consumed by Task 3's tab components.

- [ ] **Step 1: Write the example report constants**

Copied verbatim from the reference docs (source noted in comments).

```typescript
// dashboard/src/exampleReports.ts

// Verbatim from ../../references/report-area.md
export const EXAMPLE_AREA_REPORT = `Report : area
Design : top_module
Version: T-2022.03-SP5
Date   : ...

Library(s) Used:
    saed32rvt_ss1p05v0c (File: ...)

Number of ports:                          312
Number of nets:                          8842
Number of cells:                         7615
Number of combinational cells:           5203
Number of sequential cells:              2107
Number of macros/black boxes:               2
Number of buf/inv:                       1108

Total combinational area:      12045.238400
Total noncombinational area:   18932.556800
Total buf/inv area:             2210.995200 (included above)
Total macro/black box area:    45210.880000
Net Interconnect area:          undefined (Wire load model not compiled)

Total cell area:               76188.675200
Total area:                    undefined
`;

// Verbatim from ../../references/report-timing.md
export const EXAMPLE_TIMING_REPORT = `****************************************
Report : timing
        -path_group reg2reg
        -delay_type max
Design : top_module
Version: T-2022.03-SP5
Date   : ...
****************************************

Startpoint: u_fetch/pc_reg[12] (rising edge-triggered flip-flop clocked by CLK)
Endpoint: u_decode/instr_reg[3] (rising edge-triggered flip-flop clocked by CLK)
Path Group: reg2reg
Path Type: max

Point                                  Incr       Path
--------------------------------------------------------
clock CLK (rise edge)                  0.00       0.00
clock network delay (propagated)       0.45       0.45
u_fetch/pc_reg[12]/CK (DFFR_X1)        0.00       0.45 r
u_fetch/pc_reg[12]/Q (DFFR_X1)         0.12       0.57 f
u1/Z (BUFX2)                           0.08       0.65 f
u2/Z (AND2X1)                          0.15       0.80 r
u_decode/instr_reg[3]/D (DFFR_X1)      0.02       0.82 r
data arrival time                                 0.82

clock CLK (rise edge)                  1.20       1.20
clock network delay (propagated)       0.48       1.68
clock uncertainty                     -0.05       1.63
u_decode/instr_reg[3]/CK (DFFR_X1)     0.00       1.63 r
library setup time                    -0.04       1.59
data required time                                1.59
--------------------------------------------------------
data required time                                1.59
data arrival time                                 -0.82
--------------------------------------------------------
slack (MET)                                        0.77
`;

// Verbatim from ../../references/report-power.md
export const EXAMPLE_POWER_REPORT = `****************************************
Report : power
        -analysis_effort low
Design : top_module
Version: T-2022.03-SP5
Date   : ...
****************************************

Global Operating Voltage = 0.9
Power-specific unit information :
    Voltage Units = 1V
    Capacitance Units = 1.000000pf
    Time Units = 1ns
    Dynamic Power Units = 1mW
    Leakage Power Units = 1nW

  Cell Internal Power  =   4.2103 mW   (42.1%)
  Net Switching Power  =   3.8871 mW   (38.9%)
                         -----------
  Total Dynamic Power  =   8.0974 mW  (98.7%)

  Cell Leakage Power   = 104.3200 uW   (1.3%)
                         -----------
  Total Power          =   8.2017 mW  (100%)
`;
```

- [ ] **Step 2: Write shared result types**

```typescript
// dashboard/src/parsers/types.ts
export type ParseResult<T> = { ok: true; data: T } | { ok: false; error: string };

export interface AreaResult {
  numPorts?: number;
  numNets?: number;
  numCells?: number;
  totalCombinationalArea: number;
  totalNoncombinationalArea: number;
  totalBufInvArea?: number;
  totalMacroArea: number;
  totalCellArea: number;
}

export interface TimingPath {
  startpoint: string;
  endpoint: string;
  pathGroup: string;
  slack: number;
  violated: boolean;
}

export interface TimingResult {
  paths: TimingPath[];
}

export interface PowerResult {
  cellInternalPowerMw: number;
  netSwitchingPowerMw: number;
  totalDynamicPowerMw: number;
  cellLeakagePowerMw: number;
  totalPowerMw: number;
}
```

- [ ] **Step 3: Write the area parser**

```typescript
// dashboard/src/parsers/parseArea.ts
import type { AreaResult, ParseResult } from "./types";

function matchNumber(text: string, label: string): number | undefined {
  const re = new RegExp(`${label}\\s*:\\s*([\\d.]+)`, "i");
  const m = text.match(re);
  if (!m) return undefined;
  const n = parseFloat(m[1]);
  return Number.isNaN(n) ? undefined : n;
}

export function parseArea(text: string): ParseResult<AreaResult> {
  const totalCombinationalArea = matchNumber(text, "Total combinational area");
  const totalNoncombinationalArea = matchNumber(
    text,
    "Total noncombinational area"
  );
  const totalMacroArea = matchNumber(text, "Total macro/black box area");
  const totalCellArea = matchNumber(text, "Total cell area");

  const missing: string[] = [];
  if (totalCombinationalArea === undefined) missing.push("Total combinational area");
  if (totalNoncombinationalArea === undefined) missing.push("Total noncombinational area");
  if (totalMacroArea === undefined) missing.push("Total macro/black box area");
  if (totalCellArea === undefined) missing.push("Total cell area");

  if (missing.length > 0) {
    return {
      ok: false,
      error: `Couldn't parse this as an area report — missing: ${missing.join(", ")}. Check it matches the format in references/report-area.md.`,
    };
  }

  return {
    ok: true,
    data: {
      numPorts: matchNumber(text, "Number of ports"),
      numNets: matchNumber(text, "Number of nets"),
      numCells: matchNumber(text, "Number of cells"),
      totalCombinationalArea: totalCombinationalArea!,
      totalNoncombinationalArea: totalNoncombinationalArea!,
      totalBufInvArea: matchNumber(text, "Total buf/inv area"),
      totalMacroArea: totalMacroArea!,
      totalCellArea: totalCellArea!,
    },
  };
}
```

- [ ] **Step 4: Write the timing parser**

```typescript
// dashboard/src/parsers/parseTiming.ts
import type { TimingResult, TimingPath, ParseResult } from "./types";

export function parseTiming(text: string): ParseResult<TimingResult> {
  const blocks = text.split(/(?=Startpoint:)/).filter((b) => b.includes("Startpoint:"));

  if (blocks.length === 0) {
    return {
      ok: false,
      error:
        "Couldn't parse this as a timing report — no 'Startpoint:' found. Check it matches the format in references/report-timing.md.",
    };
  }

  const paths: TimingPath[] = [];
  const skipped: string[] = [];

  for (const block of blocks) {
    const startpointM = block.match(/Startpoint:\s*(.+)/);
    const endpointM = block.match(/Endpoint:\s*(.+)/);
    const pathGroupM = block.match(/Path Group:\s*(.+)/);
    const slackM = block.match(/slack\s*\((MET|VIOLATED)\)\s+(-?[\d.]+)/);

    if (!startpointM || !endpointM || !slackM) {
      skipped.push(startpointM ? startpointM[1].trim() : "(unknown path)");
      continue;
    }

    paths.push({
      startpoint: startpointM[1].trim(),
      endpoint: endpointM ? endpointM[1].trim() : "(unknown)",
      pathGroup: pathGroupM ? pathGroupM[1].trim() : "(unknown)",
      slack: parseFloat(slackM[2]),
      violated: slackM[1] === "VIOLATED",
    });
  }

  if (paths.length === 0) {
    return {
      ok: false,
      error:
        "Found path start(s) but couldn't extract a slack line for any of them. Check it matches the format in references/report-timing.md.",
    };
  }

  return { ok: true, data: { paths } };
}
```

- [ ] **Step 5: Write the power parser**

```typescript
// dashboard/src/parsers/parsePower.ts
import type { PowerResult, ParseResult } from "./types";

function matchPowerMw(text: string, label: string): number | undefined {
  const re = new RegExp(`${label}\\s*=\\s*([\\d.]+)\\s*(mW|uW)`, "i");
  const m = text.match(re);
  if (!m) return undefined;
  const value = parseFloat(m[1]);
  if (Number.isNaN(value)) return undefined;
  return m[2].toLowerCase() === "uw" ? value / 1000 : value;
}

export function parsePower(text: string): ParseResult<PowerResult> {
  const cellInternalPowerMw = matchPowerMw(text, "Cell Internal Power");
  const netSwitchingPowerMw = matchPowerMw(text, "Net Switching Power");
  const totalDynamicPowerMw = matchPowerMw(text, "Total Dynamic Power");
  const cellLeakagePowerMw = matchPowerMw(text, "Cell Leakage Power");
  const totalPowerMw = matchPowerMw(text, "Total Power");

  const missing: string[] = [];
  if (cellInternalPowerMw === undefined) missing.push("Cell Internal Power");
  if (netSwitchingPowerMw === undefined) missing.push("Net Switching Power");
  if (cellLeakagePowerMw === undefined) missing.push("Cell Leakage Power");
  if (totalPowerMw === undefined) missing.push("Total Power");

  if (missing.length > 0) {
    return {
      ok: false,
      error: `Couldn't parse this as a power report — missing: ${missing.join(", ")}. Check it matches the format in references/report-power.md.`,
    };
  }

  return {
    ok: true,
    data: {
      cellInternalPowerMw: cellInternalPowerMw!,
      netSwitchingPowerMw: netSwitchingPowerMw!,
      totalDynamicPowerMw: totalDynamicPowerMw ?? cellInternalPowerMw! + netSwitchingPowerMw!,
      cellLeakagePowerMw: cellLeakagePowerMw!,
      totalPowerMw: totalPowerMw!,
    },
  };
}
```

- [ ] **Step 6: Manually validate each parser against its own example**

```bash
cd ~/gitspace/ppa-eda-agent/dashboard
cat > /tmp/validate-parsers.mjs << 'EOF'
import { parseArea } from "./src/parsers/parseArea.ts";
import { parseTiming } from "./src/parsers/parseTiming.ts";
import { parsePower } from "./src/parsers/parsePower.ts";
import {
  EXAMPLE_AREA_REPORT,
  EXAMPLE_TIMING_REPORT,
  EXAMPLE_POWER_REPORT,
} from "./src/exampleReports.ts";

const area = parseArea(EXAMPLE_AREA_REPORT);
console.assert(area.ok, "area parse failed: " + JSON.stringify(area));
console.assert(area.ok && area.data.totalCellArea === 76188.6752, "area totalCellArea mismatch");

const timing = parseTiming(EXAMPLE_TIMING_REPORT);
console.assert(timing.ok, "timing parse failed: " + JSON.stringify(timing));
console.assert(timing.ok && timing.data.paths.length === 1, "expected 1 path");
console.assert(timing.ok && timing.data.paths[0].slack === 0.77, "slack mismatch");
console.assert(timing.ok && timing.data.paths[0].violated === false, "should be MET");

const power = parsePower(EXAMPLE_POWER_REPORT);
console.assert(power.ok, "power parse failed: " + JSON.stringify(power));
console.assert(power.ok && power.data.cellLeakagePowerMw === 0.1043, "leakage mW conversion wrong");

console.log("all parser checks passed");
EOF
npx tsx /tmp/validate-parsers.mjs
```

Expected: `all parser checks passed`, no assertion failures. If `tsx` isn't
available, run `npm install --save-dev tsx` first.

- [ ] **Step 7: Commit**

```bash
cd ~/gitspace/ppa-eda-agent
git add dashboard/src/exampleReports.ts dashboard/src/parsers dashboard/package.json dashboard/package-lock.json
git commit -m "Add report parsers and example data for PPA EDA dashboard"
```

---

### Task 3: Tab components with charts

**Files:**
- Create: `dashboard/src/components/AreaTab.tsx`
- Create: `dashboard/src/components/TimingTab.tsx`
- Create: `dashboard/src/components/PowerTab.tsx`
- Create: `dashboard/src/components/ReportInput.tsx`
- Create: `dashboard/src/components/Tabs.css`

**Interfaces:**
- Consumes: parsers and types from Task 2.
- Produces: `AreaTab`, `TimingTab`, `PowerTab` default-export components, no props — used by `App.tsx` in Task 4.

- [ ] **Step 1: Write the shared input component**

```tsx
// dashboard/src/components/ReportInput.tsx
interface ReportInputProps {
  value: string;
  onChange: (value: string) => void;
  onLoadExample: () => void;
  placeholder: string;
}

export default function ReportInput({
  value,
  onChange,
  onLoadExample,
  placeholder,
}: ReportInputProps) {
  return (
    <div className="report-input">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={10}
      />
      <button onClick={onLoadExample}>Load example</button>
    </div>
  );
}
```

- [ ] **Step 2: Write the Area tab**

```tsx
// dashboard/src/components/AreaTab.tsx
import { useMemo, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from "recharts";
import ReportInput from "./ReportInput";
import { parseArea } from "../parsers/parseArea";
import { EXAMPLE_AREA_REPORT } from "../exampleReports";
import "./Tabs.css";

export default function AreaTab() {
  const [text, setText] = useState("");
  const result = useMemo(() => (text.trim() ? parseArea(text) : null), [text]);

  const chartData = result?.ok
    ? [
        {
          name: "Area",
          combinational:
            result.data.totalCombinationalArea - (result.data.totalBufInvArea ?? 0),
          bufInv: result.data.totalBufInvArea ?? 0,
          noncombinational: result.data.totalNoncombinationalArea,
          macro: result.data.totalMacroArea,
        },
      ]
    : [];

  return (
    <div className="tab">
      <ReportInput
        value={text}
        onChange={setText}
        onLoadExample={() => setText(EXAMPLE_AREA_REPORT)}
        placeholder="Paste Design Compiler report_area output here…"
      />
      {result && !result.ok && <div className="tab__error">{result.error}</div>}
      {result?.ok && (
        <>
          <table className="tab__summary">
            <tbody>
              <tr><td>Total cell area</td><td>{result.data.totalCellArea.toLocaleString()}</td></tr>
              <tr><td>Combinational</td><td>{result.data.totalCombinationalArea.toLocaleString()}</td></tr>
              <tr><td>Noncombinational</td><td>{result.data.totalNoncombinationalArea.toLocaleString()}</td></tr>
              <tr><td>Macro/black box</td><td>{result.data.totalMacroArea.toLocaleString()}</td></tr>
              {result.data.totalBufInvArea !== undefined && (
                <tr><td>buf/inv (subset of combinational)</td><td>{result.data.totalBufInvArea.toLocaleString()}</td></tr>
              )}
              {result.data.numCells !== undefined && (
                <tr><td>Number of cells</td><td>{result.data.numCells.toLocaleString()}</td></tr>
              )}
            </tbody>
          </table>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData} layout="vertical">
              <XAxis type="number" />
              <YAxis type="category" dataKey="name" />
              <Tooltip />
              <Legend />
              <Bar dataKey="combinational" stackId="a" fill="#4f8ef7" name="Combinational" />
              <Bar dataKey="bufInv" stackId="a" fill="#a8c9fb" name="buf/inv" />
              <Bar dataKey="noncombinational" stackId="a" fill="#f7934f" name="Noncombinational" />
              <Bar dataKey="macro" stackId="a" fill="#7ed992" name="Macro/black box" />
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Write the Timing tab**

```tsx
// dashboard/src/components/TimingTab.tsx
import { useMemo, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer } from "recharts";
import ReportInput from "./ReportInput";
import { parseTiming } from "../parsers/parseTiming";
import { EXAMPLE_TIMING_REPORT } from "../exampleReports";
import "./Tabs.css";

export default function TimingTab() {
  const [text, setText] = useState("");
  const result = useMemo(() => (text.trim() ? parseTiming(text) : null), [text]);

  const sortedPaths = result?.ok
    ? [...result.data.paths].sort((a, b) => a.slack - b.slack)
    : [];
  const wns = sortedPaths.length > 0 ? sortedPaths[0].slack : null;
  const violatedCount = sortedPaths.filter((p) => p.violated).length;

  const chartData = sortedPaths.map((p) => ({
    name: `${p.startpoint} → ${p.endpoint}`,
    slack: p.slack,
  }));

  return (
    <div className="tab">
      <ReportInput
        value={text}
        onChange={setText}
        onLoadExample={() => setText(EXAMPLE_TIMING_REPORT)}
        placeholder="Paste PrimeTime report_timing output here (one or more paths)…"
      />
      {result && !result.ok && <div className="tab__error">{result.error}</div>}
      {result?.ok && (
        <>
          <div className="tab__meta">
            <span>WNS: {wns !== null ? wns.toFixed(2) : "—"}</span>
            <span>Violated paths: {violatedCount} / {sortedPaths.length}</span>
          </div>
          <ResponsiveContainer width="100%" height={Math.max(150, sortedPaths.length * 40)}>
            <BarChart data={chartData} layout="vertical">
              <XAxis type="number" />
              <YAxis type="category" dataKey="name" width={220} tick={{ fontSize: 10 }} />
              <Tooltip />
              <Bar dataKey="slack">
                {chartData.map((entry, i) => (
                  <Cell key={i} fill={entry.slack < 0 ? "#e5484d" : "#3dd68c"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Write the Power tab**

```tsx
// dashboard/src/components/PowerTab.tsx
import { useMemo, useState } from "react";
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from "recharts";
import ReportInput from "./ReportInput";
import { parsePower } from "../parsers/parsePower";
import { EXAMPLE_POWER_REPORT } from "../exampleReports";
import "./Tabs.css";

const COLORS = ["#4f8ef7", "#f7934f", "#8c8c8c"];

export default function PowerTab() {
  const [text, setText] = useState("");
  const result = useMemo(() => (text.trim() ? parsePower(text) : null), [text]);

  const chartData = result?.ok
    ? [
        { name: "Internal", value: result.data.cellInternalPowerMw },
        { name: "Switching", value: result.data.netSwitchingPowerMw },
        { name: "Leakage", value: result.data.cellLeakagePowerMw },
      ]
    : [];

  return (
    <div className="tab">
      <ReportInput
        value={text}
        onChange={setText}
        onLoadExample={() => setText(EXAMPLE_POWER_REPORT)}
        placeholder="Paste PrimePower report_power output here…"
      />
      {result && !result.ok && <div className="tab__error">{result.error}</div>}
      {result?.ok && (
        <>
          <table className="tab__summary">
            <tbody>
              <tr><td>Total power</td><td>{result.data.totalPowerMw.toFixed(4)} mW</td></tr>
              <tr><td>Total dynamic power</td><td>{result.data.totalDynamicPowerMw.toFixed(4)} mW</td></tr>
              <tr><td>Cell internal power</td><td>{result.data.cellInternalPowerMw.toFixed(4)} mW</td></tr>
              <tr><td>Net switching power</td><td>{result.data.netSwitchingPowerMw.toFixed(4)} mW</td></tr>
              <tr><td>Cell leakage power</td><td>{result.data.cellLeakagePowerMw.toFixed(4)} mW</td></tr>
            </tbody>
          </table>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie data={chartData} dataKey="value" nameKey="name" outerRadius={90} label>
                {chartData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Write shared tab styling**

```css
/* dashboard/src/components/Tabs.css */
.tab {
  padding: 1rem;
}
.report-input {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 1rem;
}
.report-input textarea {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.8rem;
  padding: 0.5rem;
}
.report-input button {
  align-self: flex-start;
}
.tab__error {
  color: #b00020;
  margin-bottom: 1rem;
}
.tab__summary {
  border-collapse: collapse;
  margin-bottom: 1rem;
}
.tab__summary td {
  padding: 0.2rem 0.8rem 0.2rem 0;
  font-size: 0.9rem;
}
.tab__summary td:first-child {
  color: #555;
}
.tab__meta {
  display: flex;
  gap: 1.5rem;
  margin-bottom: 1rem;
  font-size: 0.9rem;
}
```

- [ ] **Step 6: Commit**

```bash
cd ~/gitspace/ppa-eda-agent
git add dashboard/src/components
git commit -m "Add Area/Timing/Power tab components with recharts visualizations"
```

---

### Task 4: App shell and verification

**Files:**
- Modify: `dashboard/src/App.tsx`
- Modify: `dashboard/src/App.css`
- Modify: `dashboard/index.html`
- Modify: `dashboard/src/index.css`

**Interfaces:**
- Consumes: `AreaTab`, `TimingTab`, `PowerTab` from Task 3.

- [ ] **Step 1: Write App.tsx with tab navigation**

```tsx
// dashboard/src/App.tsx
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
        <h1>ppa-eda-analyst</h1>
        <p>Paste Synopsys reports to visualize Power / Performance / Area</p>
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
```

- [ ] **Step 2: Write App.css**

```css
/* dashboard/src/App.css */
.app {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  font-family: system-ui, sans-serif;
}
.app__header {
  padding: 1rem 1.5rem;
  border-bottom: 1px solid #ddd;
}
.app__header h1 {
  margin: 0;
  font-size: 1.3rem;
}
.app__header p {
  margin: 0.2rem 0 0;
  color: #666;
  font-size: 0.9rem;
}
.app__tabs {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1.5rem 0;
  border-bottom: 1px solid #ddd;
}
.app__tab {
  padding: 0.5rem 1rem;
  border: none;
  background: none;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  font-size: 0.95rem;
}
.app__tab--active {
  border-bottom-color: #4f8ef7;
  font-weight: 600;
}
.app__main {
  flex: 1;
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
}
```

- [ ] **Step 3: Update index.html title**

Change `<title>dashboard</title>` (or whatever the scaffold generated) to
`<title>ppa-eda-analyst dashboard</title>`.

- [ ] **Step 4: Trim index.css scaffold boilerplate**

Vite's `react-ts` template ships an `index.css` with a fixed-width, centered
`#root` and oversized headings (same issue hit in the `ppa-agent` dashboard
build). Read the generated file and remove/replace any `#root { width:
...px; text-align: center; ... }` and oversized `h1`/`h2` rules so they
don't fight with `App.css`'s full-width layout — replace with the same
minimal reset used in `~/gitspace/ppa-agent/dashboard/src/index.css`:

```css
:root {
  color-scheme: light dark;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
* { box-sizing: border-box; }
html, body, #root { margin: 0; height: 100%; }
code {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.85em;
  padding: 0.1em 0.35em;
  background: #f4f3ec;
  border-radius: 4px;
}
```

Also remove any now-unused scaffold assets (`src/assets/*.svg`, etc.) that
`App.tsx` no longer imports, the same way the `ppa-agent` dashboard build
did — check with `grep -rl "assets/" dashboard/src dashboard/index.html`
first to confirm nothing else references them before deleting.

- [ ] **Step 5: Build and verify**

```bash
cd ~/gitspace/ppa-eda-agent/dashboard
npm run build
```

Expected: builds with no TypeScript errors.

- [ ] **Step 6: Start dev server and smoke-test with curl**

```bash
nohup npm run dev > /tmp/ppa-eda-dashboard-dev.log 2>&1 &
sleep 4
cat /tmp/ppa-eda-dashboard-dev.log
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/
```

Expected: Vite prints a local URL; curl returns `200`. If port 5173 is
taken, note whatever port Vite actually picked (check the log) and use
that for the next step.

- [ ] **Step 7: Attempt a real browser check**

If claude-in-chrome is connected this session, open the dev server URL,
click "Load example" on each of the three tabs, and confirm each chart
renders with sensible-looking proportions and the Timing tab's single
example path shows as a green (MET) bar. If the extension is not
connected, say so explicitly rather than claiming visual confirmation —
curl/build success is not the same as a rendered UI check.

- [ ] **Step 8: Commit**

```bash
cd ~/gitspace/ppa-eda-agent
git add dashboard/src/App.tsx dashboard/src/App.css dashboard/index.html dashboard/src/index.css dashboard/src/assets
git commit -m "Wire up PPA EDA dashboard app shell with tab navigation"
```

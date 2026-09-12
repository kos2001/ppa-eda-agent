// The schematic, shown — and run from where it is shown.
//
// The custom flow starts at a schematic (see pipeline/custom_bridge.py's
// netlist_schematic), and until now nothing in this console displayed
// one. A flow whose source you cannot look at is one you have to take on
// trust, which is the opposite of what this repo is for: the digital
// half already renders its real GDS on the pipeline page for exactly
// that reason.
//
// The drawing is xschem's own SVG export, not a second renderer written
// here. A redrawing of the .sch by this file would be a second thing
// that can disagree with the netlist, and the whole point of starting at
// the schematic is that one source produces both.
//
// It is also a control surface, not a picture (soul.md): the same page
// netlists the schematic and simulates it, so what you measure is always
// the drawing in front of you rather than a deck that may predate it.
import { useCallback, useEffect, useState } from "react";
import { BACKEND } from "./EdaShell";
import { log } from "../console/log";
import "./SchematicTab.css";

type Cell = { design: string; cell: string; id: string; simulatable: boolean };

type RunResult = {
  netlist: { status: string; errors: string[]; metadata: Record<string, unknown> };
  sim?: {
    status: string;
    errors: string[];
    execution_time: number | null;
    metadata: { measurements?: Record<string, number>; corner?: string };
  };
};

const CORNERS = ["tt", "ss", "ff", "sf", "fs"];

/** Engineering notation, because these are seconds and volts and amps
 *  spanning ten decades — 6.8e-11 is a number you have to decode, 68.2 ps
 *  is one you can read. */
function eng(value: number, unit: string): string {
  const abs = Math.abs(value);
  const steps: [number, string][] = [
    [1e-12, "p"], [1e-9, "n"], [1e-6, "µ"], [1e-3, "m"], [1, ""],
  ];
  let chosen = steps[steps.length - 1];
  for (const step of steps) {
    if (abs < step[0] * 1000) { chosen = step; break; }
  }
  return `${(value / chosen[0]).toFixed(3)} ${chosen[1]}${unit}`;
}

const UNITS: Record<string, string> = {
  vtrip: "V", tphl: "s", tplh: "s", ivdd_avg: "A",
};

export default function SchematicTab() {
  const [cells, setCells] = useState<Cell[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [corner, setCorner] = useState("tt");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${BACKEND}/analog/cells`)
      .then((r) => r.json())
      .then((body) => {
        if (cancelled) return;
        setCells(body.cells ?? []);
        // A testbench first: it is the one that can actually be run, and
        // landing on a cell with a disabled button reads as broken.
        const first = (body.cells ?? []).find((c: Cell) => c.simulatable)
          ?? (body.cells ?? [])[0];
        if (first) setSelected(first.id);
      })
      .catch((err) => !cancelled && setError(String(err.message ?? err)));
    return () => { cancelled = true; };
  }, []);

  const run = useCallback(async () => {
    if (!selected) return;
    setRunning(true);
    setError(null);
    setResult(null);
    log("cmd", "analog", `netlist + simulate ${selected} @ ${corner}`);
    try {
      const res = await fetch(`${BACKEND}/analog/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cell: selected, corner }),
      });
      const body = await res.json();
      if (body.error) throw new Error(body.error);
      setResult(body);
      const meas = body.sim?.metadata?.measurements ?? {};
      log(body.sim?.status === "success" ? "info" : "error", "analog",
          `${selected} @ ${corner}: ` +
          (Object.keys(meas).length
            ? Object.entries(meas).map(([k, v]) => `${k}=${v}`).join("  ")
            : (body.sim?.errors?.[0] ?? body.netlist?.errors?.[0] ?? "no measurements")));
    } catch (err) {
      setError(String((err as Error).message ?? err));
      log("error", "analog", String((err as Error).message ?? err));
    } finally {
      setRunning(false);
    }
  }, [selected, corner]);

  const current = cells?.find((c) => c.id === selected) ?? null;
  const measurements = result?.sim?.metadata?.measurements ?? null;

  return (
    <div className="tab schematic">
      <section className="panel schematic__panel">
        <span className="panel__title">Schematic</span>
        <div className="schematic__body">
          <nav className="schematic__cells" aria-label="cells">
            {cells === null && <p className="schematic__hint">Loading cells…</p>}
            {cells?.length === 0 && (
              <p className="schematic__hint">
                No schematics under <code>pipeline/analog/</code>.
              </p>
            )}
            {cells?.map((c) => (
              <button
                type="button"
                key={c.id}
                className={c.id === selected ? "schematic__cell is-active" : "schematic__cell"}
                onClick={() => { setSelected(c.id); setResult(null); setError(null); }}
              >
                <b>{c.cell}</b>
                <span>{c.design}</span>
                {c.simulatable && <em>tb</em>}
              </button>
            ))}
          </nav>

          <div className="schematic__view">
            {selected ? (
              // <img> rather than inlined markup: the SVG is xschem's
              // file, unmodified except for the viewBox that lets it
              // scale, and keeping it a resource means the browser
              // caches it and the page never has to parse it.
              <img
                className="schematic__svg"
                src={`${BACKEND}/analog/svg?cell=${encodeURIComponent(selected)}`}
                alt={`schematic of ${selected}`}
              />
            ) : (
              <p className="schematic__hint">Select a cell.</p>
            )}
          </div>
        </div>
      </section>

      <section className="panel schematic__panel">
        <span className="panel__title">Netlist &amp; simulate</span>
        <div className="schematic__run">
          <div className="schematic__controls">
            <label>
              corner
              <select value={corner} onChange={(e) => setCorner(e.target.value)}>
                {CORNERS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <button
              type="button"
              className="schematic__go"
              onClick={run}
              disabled={!current?.simulatable || running}
              title={current?.simulatable
                ? "xschem netlists the schematic, then ngspice runs it"
                : "this cell carries no analysis — open its testbench"}
            >
              {running ? "running…" : "netlist & simulate"}
            </button>
            {current && !current.simulatable && (
              <span className="schematic__hint">
                {current.cell} is a cell, not a testbench — it has no
                <code>.control</code> block to run.
              </span>
            )}
          </div>

          {error && <p className="schematic__error">{error}</p>}

          {result && (
            <div className="schematic__result">
              <p className="schematic__hint">
                netlist <b>{result.netlist.status}</b>
                {result.sim && <> · simulation <b>{result.sim.status}</b>
                  {result.sim.execution_time != null && <> in {result.sim.execution_time}s</>}
                </>}
              </p>
              {measurements && Object.keys(measurements).length > 0 ? (
                <table className="schematic__meas">
                  <tbody>
                    {Object.entries(measurements).map(([k, v]) => (
                      <tr key={k}>
                        <th scope="row">{k}</th>
                        <td>{eng(v, UNITS[k] ?? "")}</td>
                        <td className="schematic__raw">{v}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                // ngspice exits 0 with a measure that failed, so an
                // empty table is a real outcome worth naming rather
                // than an empty state to hide.
                <p className="schematic__error">
                  No measurements came back.{" "}
                  {(result.sim?.errors ?? result.netlist.errors)[0] ?? ""}
                </p>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

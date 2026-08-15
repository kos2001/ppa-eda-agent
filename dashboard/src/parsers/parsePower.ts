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

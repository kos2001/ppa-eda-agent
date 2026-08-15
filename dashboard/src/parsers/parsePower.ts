import type { PowerResult, ParseResult } from "./types";

function matchPowerMw(text: string, label: string): number | undefined {
  const re = new RegExp(`${label}\\s*=\\s*([\\d.]+)\\s*(mW|uW)`, "i");
  const m = text.match(re);
  if (!m) return undefined;
  const value = parseFloat(m[1]);
  if (Number.isNaN(value)) return undefined;
  return m[2].toLowerCase() === "uw" ? value / 1000 : value;
}

// OpenSTA's plain `report_power` output is a "Group" table (Internal /
// Switching / Leakage / Total columns, in Watts) rather than PrimePower's
// "Cell Internal Power = X mW" lines — confirmed against a real live
// OpenSTA run, not assumed. Its "Total" row sums every group
// (Sequential/Combinational/Clock/Macro/Pad).
function parseGroupTablePower(text: string): PowerResult | undefined {
  const totalRowM = text.match(
    /^Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)/m
  );
  if (!totalRowM) return undefined;

  const internalW = parseFloat(totalRowM[1]);
  const switchingW = parseFloat(totalRowM[2]);
  const leakageW = parseFloat(totalRowM[3]);
  const totalW = parseFloat(totalRowM[4]);
  if ([internalW, switchingW, leakageW, totalW].some(Number.isNaN)) {
    return undefined;
  }

  const wToMw = (w: number) => w * 1000;
  return {
    cellInternalPowerMw: wToMw(internalW),
    netSwitchingPowerMw: wToMw(switchingW),
    totalDynamicPowerMw: wToMw(internalW + switchingW),
    cellLeakagePowerMw: wToMw(leakageW),
    totalPowerMw: wToMw(totalW),
  };
}

export function parsePower(text: string): ParseResult<PowerResult> {
  const groupTable = parseGroupTablePower(text);
  if (groupTable) {
    return { ok: true, data: groupTable };
  }

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

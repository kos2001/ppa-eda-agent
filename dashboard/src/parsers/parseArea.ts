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

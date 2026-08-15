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

  for (const block of blocks) {
    const startpointM = block.match(/Startpoint:\s*(.+)/);
    const endpointM = block.match(/Endpoint:\s*(.+)/);
    const pathGroupM = block.match(/Path Group:\s*(.+)/);
    const slackM = block.match(/slack\s*\((MET|VIOLATED)\)\s+(-?[\d.]+)/);

    if (!startpointM || !slackM) {
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

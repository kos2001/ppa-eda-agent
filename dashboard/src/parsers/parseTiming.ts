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
    // Slack number position relative to "slack (STATUS)" differs by tool:
    // Synopsys PrimeTime puts it after ("slack (MET)   0.77"), OpenSTA
    // puts it before ("228.48   slack (MET)") — confirmed against real
    // OpenSTA test/prima3.ok output, not assumed. Match the status first,
    // then pull whichever number is on the same line.
    const slackLineM = block.match(/^.*slack\s*\((MET|VIOLATED)\).*$/m);
    const slackNumberM = slackLineM
      ? slackLineM[0].match(/(-?[\d.]+)/)
      : null;

    if (!startpointM || !slackLineM || !slackNumberM) {
      continue;
    }

    paths.push({
      startpoint: startpointM[1].trim(),
      endpoint: endpointM ? endpointM[1].trim() : "(unknown)",
      pathGroup: pathGroupM ? pathGroupM[1].trim() : "(unknown)",
      slack: parseFloat(slackNumberM[1]),
      violated: slackLineM[1] === "VIOLATED",
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

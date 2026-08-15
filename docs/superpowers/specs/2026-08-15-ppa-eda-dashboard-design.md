# PPA EDA Dashboard — Design Spec

## Goal

Build a frontend that visualizes semiconductor PPA (Power, Performance,
Area) data extracted from pasted Synopsys report text, giving the
`ppa-eda-analyst` subagent's domain a visual counterpart the way the
`ppa-agent` project's `dashboard/` gives the Ansible pipeline one.

## Scope

- New `dashboard/` directory in `~/gitspace/ppa-eda-agent`, a self-contained
  Vite React TypeScript app (no backend server — everything client-side).
- Three tabs: Area, Timing, Power. Each: a textarea for pasting report
  text, a "Load example" button, and a chart + key-metric summary once
  parsed.
- Client-side parsers for each report type, built from the structural
  knowledge already written in `references/report-{area,timing,power}.md`.

Out of scope:
- No file upload (text-paste only, per decision).
- No server-side parsing, no persistence — parse-and-render only, state
  resets on reload.
- No attempt to parse arbitrary/malformed report variants beyond the
  structure documented in the reference docs — if parsing fails, show a
  clear error rather than guessing.

## 1. Parsers

`dashboard/src/parsers/`, one file per report type, each exporting a
`parse*(text: string): Result` function (`Result` is either the parsed
data or a `{error: string}` shape — never throws, since parse failures on
pasted user text are an expected, common case, not exceptional).

**`parseArea.ts`** — extracts from `report_area` text: total combinational
area, total noncombinational area, total buf/inv area, total macro/black
box area, total cell area, number of cells/ports/nets. Matches the labeled
lines (`Total combinational area:`, etc.) per `report-area.md`'s example
format — tolerant of extra whitespace, not of structurally different
report variants.

**`parseTiming.ts`** — extracts one or more path entries from
`report_timing` text: startpoint, endpoint, path group, data arrival time,
data required time, slack, and violated/met status. Handles multiple
concatenated path reports in one paste (splits on `Startpoint:` occurrences,
matching `report-timing.md`'s example structure of multiple path blocks).

**`parsePower.ts`** — extracts from `report_power` text: cell internal
power, net switching power, total dynamic power, cell leakage power, total
power (with units, since PrimePower reports mix mW/uW — normalize
everything to mW for consistent charting, converting `uW` values by
dividing by 1000).

## 2. Visualization per tab

**Area tab**: a stacked bar (single bar, segments = combinational /
noncombinational / buf-inv subset / macro) plus a summary table of the raw
numbers. Buf/inv is shown as a lighter-shaded sub-segment inside
combinational (matching `report-area.md`'s "included above" relationship),
not double-counted in the stack total.

**Timing tab**: a horizontal bar chart, one bar per parsed path, bar length
= slack, colored red for negative (violated) and green for positive (met).
Sorted worst-slack-first. A summary line shows WNS (most negative slack
found) and count of violated paths.

**Power tab**: a pie chart of the three power components (internal /
switching / leakage, all normalized to mW) plus a summary table showing
each value and its reported percentage, and total power.

## 3. Error handling

If a parser can't find the expected labeled fields, the tab shows "Couldn't
parse this as a `<type>` report — check it matches the format in
references/report-`<type>`.md" rather than a blank chart or a JS exception.
Partial matches (e.g. area found but macro area missing) render what was
found and note which fields are missing, rather than failing entirely.

## 4. Example data

Each tab's "Load example" button fills the textarea with the exact
illustrative snippet already present in the matching `references/*.md`
file (kept as literal strings in the parser test file / a shared
`exampleReports.ts` module, sourced from those docs so they can't drift out
of sync silently — a comment notes to keep them matching by hand since
there's no automated sync mechanism).

## Testing / validation

- `npm run build` succeeds (TypeScript compiles clean).
- Each parser has an inline manual check: running it against its own
  example snippet (from `exampleReports.ts`) must produce the numbers
  visible in that literal string.
- Manual browser verification: paste each example, confirm the
  corresponding chart renders with correct-looking proportions (not
  automatable without a real browser session).

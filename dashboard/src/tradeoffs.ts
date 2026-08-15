// Directional trade-offs for common EDA optimization techniques, sourced
// directly from the "Relationship to the other PPA legs" sections of
// references/report-{area,timing,power}.md. Values are qualitative
// (-1 / 0 / +1), not measured magnitudes — this is a teaching aid, not a
// prediction of how much a given fix will cost or save.
//
// Sign convention:
//   area / power : +1 = costs more, -1 = saves, 0 = no meaningful change
//   timing       : +1 = slack improves (better), -1 = slack worsens, 0 = no change

export interface Tradeoff {
  technique: string;
  area: -1 | 0 | 1;
  timing: -1 | 0 | 1;
  power: -1 | 0 | 1;
  note: string;
  source: string;
}

export const TRADEOFFS: Tradeoff[] = [
  {
    technique: "Upsizing",
    area: 1,
    timing: 1,
    power: 1,
    note: "Bigger cells drive a failing path faster, but cost area and draw more dynamic power on their output nets.",
    source: "report-area.md / report-timing.md",
  },
  {
    technique: "Buffering / pipelining",
    area: 1,
    timing: 1,
    power: 1,
    note: "Buffer insertion or an extra pipeline stage is the most robust setup fix, at the largest area and power cost.",
    source: "report-area.md / report-timing.md",
  },
  {
    technique: "Clock gating",
    area: 1,
    timing: 0,
    power: -1,
    note: "Small extra logic (gating cell + enable), usually a big win when idle cycles are common.",
    source: "report-power.md",
  },
  {
    technique: "Multi-Vt swap",
    area: -1,
    timing: 0,
    power: -1,
    note: "Moving non-critical cells to a higher-Vt, smaller/slower library cuts area and leakage without touching timing.",
    source: "report-area.md / report-power.md",
  },
];

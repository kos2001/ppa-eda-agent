// One glyph per sidebar destination.
//
// Inline SVG rather than an icon package: this app has three runtime
// dependencies and none of them is a font or icon set, and a dozen
// 16px marks are not worth a fourth. They inherit `currentColor`, so
// the active, hover and dim states that already exist on the nav apply
// to the icon without a second set of rules.
//
// Drawn from what each page shows rather than from a generic category —
// the timing mark is a clock, the progress mark is a rising line, the
// pipeline mark is a die with a route running through it. Collapsed,
// the icon is the only label the reader gets, so a mark that could
// belong to any page would make the collapsed rail unusable.
import type { ReactElement } from "react";

const BOX = {
  viewBox: "0 0 16 16",
  width: 16,
  height: 16,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.4,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

/** A die with pins and a route across it — the layout pipeline. */
const pipeline = (
  <svg {...BOX}>
    <rect x="4" y="4" width="8" height="8" rx="1" />
    <path d="M4 7H2M4 10H2M14 7h-2M14 10h-2M7 4V2M10 4V2M7 14v-2M10 14v-2" />
  </svg>
);

/** A trace with a beat in it — is anything wrong right now. */
const health = (
  <svg {...BOX}>
    <path d="M1.5 8h3l1.5-4 2.5 8 1.5-4h4.5" />
  </svg>
);

/** A line that climbs — the progress page's whole claim. */
const progress = (
  <svg {...BOX}>
    <path d="M2 13V3M2 13h12" />
    <path d="M4.5 10.5l3-3 2.5 2 3.5-4" />
  </svg>
);

/** Stacked layers — the case store and what is read from it. */
const lineage = (
  <svg {...BOX}>
    <ellipse cx="8" cy="3.75" rx="5.25" ry="2.25" />
    <path d="M2.75 3.75v8.5c0 1.24 2.35 2.25 5.25 2.25s5.25-1.01 5.25-2.25v-8.5" />
    <path d="M2.75 8c0 1.24 2.35 2.25 5.25 2.25S13.25 9.24 13.25 8" />
  </svg>
);

/** A question in a bubble. */
const ask = (
  <svg {...BOX}>
    <path d="M14 10.5a1.5 1.5 0 0 1-1.5 1.5H6l-3 2.5V12H3.5A1.5 1.5 0 0 1 2 10.5v-7A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5z" />
    <path d="M6.5 6a1.6 1.6 0 1 1 1.9 1.6V8.6" />
    <path d="M8.4 10.4h.01" />
  </svg>
);

/** An open book — the manual. */
const manual = (
  <svg {...BOX}>
    <path d="M8 4.2C6.9 3.2 5.4 2.75 3 2.75v9.5c2.4 0 3.9.45 5 1.45 1.1-1 2.6-1.45 5-1.45v-9.5c-2.4 0-3.9.45-5 1.45z" />
    <path d="M8 4.2v9.5" />
  </svg>
);

/** A waveform under a play head — running a real simulation. */
const simulate = (
  <svg {...BOX}>
    <path d="M1.5 11h2V5h3v6h3V7h3v4h2" />
  </svg>
);

/** A filled rectangle — area is the one metric that is a shape. */
const area = (
  <svg {...BOX}>
    <rect x="2.5" y="2.5" width="11" height="11" rx="1" />
    <path d="M2.5 9.5h11M9.5 2.5v11" />
  </svg>
);

const timing = (
  <svg {...BOX}>
    <circle cx="8" cy="8" r="5.75" />
    <path d="M8 4.75V8l2.25 1.75" />
  </svg>
);

const power = (
  <svg {...BOX}>
    <path d="M9 1.5 3.5 9H8l-1 5.5L12.5 7H8z" />
  </svg>
);

/** Two pans — what trade-offs are. */
const tradeoffs = (
  <svg {...BOX}>
    <path d="M8 2.5v11M4 13.5h8" />
    <path d="M2 5.5h12M4.5 5.5 2.5 9.5h4zM11.5 5.5l-2 4h4z" />
  </svg>
);

/** A spark — the live agent, distinct from the pages that only read. */
const diagnosis = (
  <svg {...BOX}>
    <path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3M3.4 3.4l2.1 2.1M10.5 10.5l2.1 2.1M12.6 3.4l-2.1 2.1M5.5 10.5l-2.1 2.1" />
  </svg>
);

/** The collapse control's own mark, pointing the way the rail will move. */
const expand = (
  <svg {...BOX}><path d="M6 3.5 10.5 8 6 12.5" /></svg>
);
const collapse = (
  <svg {...BOX}><path d="M10 3.5 5.5 8 10 12.5" /></svg>
);

// One export, and it is data rather than a component: a module that
// exports both loses fast refresh, which oxlint flags. Every mark here
// is a static element, so there was never a component to write.
export const NAV_ICONS: Record<string, ReactElement> = {
  pipeline, health, progress, lineage, ask, manual,
  simulate, area, timing, power, tradeoffs, diagnosis,
  expand, collapse,
};

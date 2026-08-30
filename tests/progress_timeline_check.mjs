// Runs the dashboard's progress-timeline helpers over the real
// reference-db/cases/ store and prints what they produced, for
// test_progress_timeline.py to assert on.
//
// Loaded through tsx, and kept in a module the component imports rather
// than in the component, so node never has to resolve a stylesheet —
// the same split as markdownParse.ts and caseGrouping.ts.
//
// Against the real store, because the shape that makes this hard is one
// nobody designed: the store spans two technologies whose areas differ
// by 3.5x, so a curve that mixes them reads as a regression that never
// happened.
import {
  coverageCurve,
  frontiers,
} from "../dashboard/src/components/progressTimeline.ts";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const casesDir = process.argv[2];
const cases = readdirSync(casesDir)
  .filter((name) => name.endsWith(".json"))
  .map((name) => {
    const parsed = JSON.parse(readFileSync(path.join(casesDir, name), "utf8"));
    parsed.file = name;
    return parsed;
  });

console.log(
  JSON.stringify(
    {
      coverage: coverageCurve(cases),
      frontiers: frontiers(cases),
    },
    null,
    2
  )
);

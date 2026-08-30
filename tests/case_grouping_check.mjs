// Runs the dashboard's case-grouping helpers over the real
// reference-db/cases/ store and prints what they produced, for
// test_case_grouping.py to assert on.
//
// The helpers are TypeScript, so they load through tsx. They live in
// caseGrouping.ts rather than PipelineTab.tsx for the same reason
// markdownParse.ts is split out of Markdown.tsx: the component imports
// a stylesheet, which node cannot resolve.
//
// Run against the real store rather than a fixture. The grouping exists
// because the store grew shapes nobody designed for — 41 cases sharing
// one date, a case whose only candidate has nothing to vary — and a
// fixture would be written by whoever wrote the helpers and would share
// their blind spots.
//
// A thin harness on purpose — every assertion lives in the Python test,
// so the project keeps one suite and one place to read.
import {
  groupByDesign,
  recordedAt,
  sweptAxis,
} from "../dashboard/src/components/caseGrouping.ts";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const casesDir = process.argv[2];
const cases = readdirSync(casesDir)
  .filter((name) => name.endsWith(".json"))
  .map((name) => {
    const parsed = JSON.parse(readFileSync(path.join(casesDir, name), "utf8"));
    // What the server attaches; the helpers read it for the timestamp
    // that tells same-day cases apart.
    parsed.file = name;
    return parsed;
  });

const groups = groupByDesign(cases);

console.log(
  JSON.stringify(
    {
      total: cases.length,
      // Per case, keyed by the file so a mismatch names the case that
      // produced it rather than an index into a list.
      perCase: Object.fromEntries(
        cases.map((c) => [
          c.file,
          { recordedAt: recordedAt(c), axis: sweptAxis(c) },
        ])
      ),
      groups: groups.map((g) => ({
        design: g.design,
        total: g.cases.length,
        passed: g.passed,
        // The order within a group is the claim "newest first" makes.
        files: g.cases.map((c) => c.file),
      })),
    },
    null,
    2
  )
);

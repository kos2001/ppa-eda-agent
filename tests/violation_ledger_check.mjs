// Runs the dashboard's violation-ledger helpers over the real
// reference-db/cases/ store and prints what they produced, for
// test_violation_ledger.py to assert on. Same split and same reason as
// progress_timeline_check.mjs: the module is loaded through tsx, and
// lives apart from the component so node never resolves a stylesheet.
import {
  countViolations,
  KINDS,
  kindsPresent,
  ledger,
  maxCount,
} from "../dashboard/src/components/violationLedger.ts";
import { groupByDesign } from "../dashboard/src/components/caseGrouping.ts";
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

// Every verdict's parse, so the test can check no counted string was
// dropped and nothing was counted that is not a count.
const parses = [];
for (const c of cases) {
  for (const it of c.iterations ?? []) {
    for (const r of it.results ?? []) {
      if (!r.verdict) continue;
      parses.push({
        file: c.file,
        tag: r.tag,
        violations: r.verdict.violations ?? [],
        ...countViolations(r.verdict.violations),
      });
    }
  }
}

const designs = {};
for (const g of groupByDesign(cases)) {
  const runs = ledger(g.cases);
  designs[g.design] = {
    runs,
    kinds: kindsPresent(runs).map((k) => k.id),
    max: maxCount(runs),
  };
}

console.log(JSON.stringify({ kinds: KINDS.map((k) => k.id), parses, designs }));

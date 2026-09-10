// Runs the dashboard's run-cost helper over the real reference-db/cases/
// store and prints what it produced, for test_run_cost.py to compare
// against collect.recorded_seconds() — the pipeline's own copy of the
// same computation.
//
// Same split as progress_timeline_check.mjs: loaded through tsx, from a
// module the component imports, so node never resolves a stylesheet.
import { runCosts } from "../dashboard/src/components/runCost.ts";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const casesDir = process.argv[2];
const cases = readdirSync(casesDir)
  .filter((name) => name.endsWith(".json"))
  .map((name) => JSON.parse(readFileSync(path.join(casesDir, name), "utf8")));

console.log(JSON.stringify(runCosts(cases)));

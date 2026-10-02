// Runs the dashboard's objective/front helper over a directory of cases and
// prints, per iteration with two or more passing candidates, which
// objectives it used and which candidates it puts on the Pareto front, for
// test_objectives_chart.py to compare with the pipeline's own computation.
//
// Loaded through tsx, from a module with no stylesheet import, so node
// never has to resolve CSS (same split as run_cost_check.mjs).
import { objectivePoints, isPassing } from "../dashboard/src/components/objectives.ts";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const casesDir = process.argv[2];
const out = [];
for (const name of readdirSync(casesDir).filter((n) => n.endsWith(".json")).sort()) {
  const c = JSON.parse(readFileSync(path.join(casesDir, name), "utf8"));
  for (const it of c.iterations ?? []) {
    if (it.results.filter(isPassing).length < 2) continue;
    const { points, used } = objectivePoints(it.results);
    out.push({
      file: name,
      iteration: it.iteration,
      used,
      front: points.filter((p) => p.onFront).map((p) => p.tag).sort(),
    });
  }
}
console.log(JSON.stringify(out));

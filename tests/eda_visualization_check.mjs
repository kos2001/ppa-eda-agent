import assert from "node:assert/strict";
import { cellCoverage } from "../dashboard/src/components/layoutGeometry.ts";
import { comparisonEvidence, checkState, finiteMetric, inspectionRows } from "../dashboard/src/components/runInspection.ts";

const cell = { inst: "fixture", master: "cell", x: 11, y: 21, w: 2, h: 2, orient: "N" };
const layout = { die: [10, 20, 14, 24], cells: [cell], nets: [] };
const tiles = cellCoverage(layout, 2);
assert.equal(tiles.length, 4);
assert.ok(tiles.every(tile => tile.coverage === 0.25));
assert.equal(tiles.reduce((area, tile) => area + tile.coverage * tile.w * tile.h, 0), 4);
const clipped = cellCoverage({ ...layout, cells: [{ ...cell, x: 9, y: 19 }] }, 2);
assert.equal(clipped.reduce((area, tile) => area + tile.coverage * tile.w * tile.h, 0), 1);
assert.equal(cellCoverage({ ...layout, cells: [{ ...cell, x: 100 }] }, 2).reduce((n, tile) => n + tile.coverage, 0), 0);
assert.equal(cellCoverage({ ...layout, cells: [cell, cell] }, 2)[0].coverage, 0.5);
for (const bins of [0, 65, 1.5]) assert.deepEqual(cellCoverage(layout, bins), []);
assert.deepEqual(cellCoverage({ ...layout, die: null }), []);
assert.deepEqual(cellCoverage({ ...layout, die: [0, 0, 0, 0] }), []);
assert.equal(finiteMetric(0), 0);
assert.equal(finiteMetric(false), null);
for (const invalid of [undefined, null, NaN, Infinity, -1, 0.5]) assert.equal(checkState(invalid), "unknown");
assert.equal(checkState(0), "clean");
assert.equal(checkState(3), "violation");
const c = (file, key) => ({ file, design: "fixture", date: "2026-10-04", iterations: [{ iteration: 1, results: [
  { tag: "repeat", overrides: {}, evaluation_provenance: key ? { complete: true, compatibility_key: key } : undefined },
]}] });
const rows = inspectionRows([c("fixture__2026-10-04__080000.json", "a"), c("fixture__2026-10-04__090000.json", "a")]);
assert.notEqual(rows[0].id, rows[1].id);
assert.ok(rows[0].pipelineCase.file.includes("090000"));
assert.ok(rows[0].technology.includes("unrecorded PDK"));
assert.equal(comparisonEvidence(rows), "compatible");
assert.equal(comparisonEvidence(inspectionRows([c("a.json", "a"), c("b.json", "b")])), "different");
assert.equal(comparisonEvidence(inspectionRows([c("a.json"), c("b.json", "b")])), "unknown");
console.log(JSON.stringify({ geometric_area_conserved: true, unknown_checks_preserved: true, source_boundaries: true }));

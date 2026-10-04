import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { normalizeBaseUrl, isApiRequest } from "../dashboard/src/api/config.ts";
import { candidateState, candidateSummary } from "../dashboard/src/components/overviewData.ts";

assert.equal(normalizeBaseUrl(" http://127.0.0.1:8124/ "), "http://127.0.0.1:8124");
assert.equal(normalizeBaseUrl("/api/"), "/api");
for (const invalid of ["//other.example/api", "file:///private/tmp", "https://key:secret@example.com", "https://example.com/?key=secret"]) {
  assert.throws(() => normalizeBaseUrl(invalid));
}
const page = "http://127.0.0.1:5174/";
assert.equal(isApiRequest("http://127.0.0.1:8124/reference-db/layouts/test.png", page, "http://127.0.0.1:8124"), true);
assert.equal(isApiRequest("http://127.0.0.1:8123/health", page, "http://127.0.0.1:8124"), false);
assert.equal(isApiRequest("/api/review/ask", page, "/api"), true);
assert.equal(isApiRequest("/apiary/file", page, "/api"), false);
assert.equal(isApiRequest("/@vite/client", page, "/api"), false);
assert.equal(isApiRequest("https://other.example/api/health", page, "/api"), false);

const clean = { passed: true, violations: [], signoff_checks: [{ key: "drc", count: 0 }] };
assert.equal(candidateState({ verdict: clean }), "clean");
assert.equal(candidateState({ verdict: { ...clean, signoff_checks: [{ key: "lvs", count: null }] } }), "unknown");
assert.equal(candidateState({ verdict: { ...clean, unverified: ["timing"] } }), "unknown");
assert.equal(candidateState({ verdict: { ...clean, signoff_checks: undefined } }), "unknown");
assert.equal(candidateState({ verdict: { ...clean, signoff_checks: [{ key: "drc", count: 2 }] } }), "violations");
assert.equal(candidateState({ error: "STA-0572" }), "error");
assert.equal(candidateState({}), "unknown");
// Model qualification remains open even when recorded physical counts are 0.
assert.equal(candidateState({ verdict: { ...clean, model_validity: { macro_arc_audit: { model_qualified: false } } } }), "unknown");

const casesDir = process.argv[2];
const cases = readdirSync(casesDir).filter(name => name.endsWith(".json"))
  .map(name => JSON.parse(readFileSync(path.join(casesDir, name), "utf8")));
const counts = candidateSummary(cases);
const total = cases.flatMap(c => c.iterations ?? []).flatMap(i => i.results ?? []).length;
assert.equal(Object.values(counts).reduce((a, b) => a + b, 0), total);
assert.ok(counts.clean > 0 && counts.violations > 0 && counts.unknown > 0 && counts.error > 0);
console.log(JSON.stringify({ counts, total }));

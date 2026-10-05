import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, utimes, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createReportCache, reportStoreKey } from "../server/report-cache.mjs";

const root = await mkdtemp(path.join(tmpdir(), "report-cache-"));
try {
  const db = path.join(root, "db");
  const designs = path.join(root, "designs");
  for (const dir of ["cases", "reviews", "layouts"]) await mkdir(path.join(db, dir), { recursive: true });
  await mkdir(path.join(designs, "counter"), { recursive: true });
  await writeFile(path.join(db, "index.json"), "{}");
  const old = path.join(db, "cases", "old.json");
  const newest = path.join(db, "cases", "new.json");
  await writeFile(old, "{}");
  await writeFile(newest, "{}");
  await utimes(newest, 2_000_000_000, 2_000_000_000);
  const getKey = () => reportStoreKey(db, designs);
  let before = await getKey();
  await writeFile(old, '{"review":true}');
  assert.notEqual(await getKey(), before, "editing an older case must invalidate");
  for (const file of [path.join(db, "index.json"), path.join(db, "reviews", "review.md"),
    path.join(db, "layouts", "layout.png"), path.join(designs, "counter", "run_spec.json")]) {
    before = await getKey();
    await writeFile(file, "changed");
    assert.notEqual(await getKey(), before, file);
  }
  before = await getKey();
  await rm(old);
  assert.notEqual(await getKey(), before, "deletion must invalidate");
  assert.equal(await getKey(), await getKey(), "unchanged inputs must reuse cache");

  let key = "a";
  let calls = 0;
  let release;
  const cache = createReportCache(async () => key);
  const produce = () => { calls++; return new Promise(resolve => { release = resolve; }); };
  const first = cache("health", produce);
  const second = cache("health", produce);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 1, "concurrent calls must share production");
  release("report");
  assert.deepEqual(await Promise.all([first, second]), ["report", "report"]);
  assert.equal(await cache("health", produce), "report");
  key = "b";
  await assert.rejects(cache("health", async () => { throw new Error("failed"); }));
  assert.equal(await cache("health", async () => "recovered"), "recovered");
  key = null;
  assert.equal(await cache("health", async () => "uncached"), "uncached");
  assert.equal(await cache("health", async () => "fresh"), "fresh");
} finally {
  await rm(root, { recursive: true, force: true });
}
console.log("report cache checks passed");

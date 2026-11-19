import assert from "node:assert/strict";
import { createSchematicJobs } from "../server/schematic-jobs.mjs";

let release;
const gate = new Promise(resolve => { release = resolve; });
let preparations = 0, renders = 0, simultaneous = 0, peak = 0, time = 0;
const jobs = createSchematicJobs({
  prepare: async () => { preparations++; await gate; },
  render: async cell => {
    renders++; peak = Math.max(peak, ++simultaneous);
    await new Promise(resolve => setTimeout(resolve, 5));
    simultaneous--;
    return cell;
  }, now: () => time, ttl: 100,
});
const first = jobs.start("analog/inv/inv", true);
assert.equal(first.state, "preparing");
assert.equal(jobs.start("analog/inv/inv", true), first);
const second = jobs.start("analog/nand2/nand2", true);
await Promise.resolve();
assert.equal(preparations, 1);
assert.equal(renders, 0);
release();
await Promise.all([first.done, second.done]);
assert.equal(renders, 2);
assert.equal(peak, 1);
assert.equal(first.state, "ready");
assert.equal(jobs.get(first.id), first);
time = 101;
assert.equal(jobs.get(first.id), undefined);

let fail = true;
const retry = createSchematicJobs({
  prepare: async () => { if (fail) throw new Error("Docker is stopped"); },
  render: async () => "SVG",
});
const failed = retry.start("cell", true);
await failed.done;
assert.equal(failed.state, "failed");
assert.equal(failed.error, "Docker is stopped");
fail = false;
const retried = retry.start("cell", true);
await retried.done;
assert.equal(retried.state, "ready");
assert.notEqual(retried.id, failed.id);
const prep = retry.start(null, false, true);
await prep.done;
assert.equal(prep.state, "ready");
assert.equal(prep.result, undefined);
console.log("Schematic jobs: shared preparation, deduplication, serialization, expiry, and retry passed");

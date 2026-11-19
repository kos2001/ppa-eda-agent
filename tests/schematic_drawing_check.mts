import assert from "node:assert/strict";
import { fetchDrawing } from "../dashboard/src/api/schematicDrawing.ts";

const source = new URL("http://localhost:8123/analog/svg?cell=analog/inv/inv");
const phases: string[] = [], urls: string[] = [];
const responses = [
  new Response(JSON.stringify({ state: "preparing", poll: "render-jobs/one" }), { status: 202 }),
  new Response(JSON.stringify({ state: "rendering", poll: "render-jobs/one" }), { status: 202 }),
  new Response("<svg/>", { headers: { "content-type": "image/svg+xml" } }),
];
const fetcher = async (url: unknown) => { urls.push(String(url)); return responses.shift()!; };
const result = await fetchDrawing(source, new AbortController().signal, phase => phases.push(phase), fetcher, async () => {});
assert.equal(result, "<svg/>");
assert.deepEqual(phases, ["preparing", "rendering"]);
assert.equal(urls[1], "http://localhost:8123/analog/render-jobs/one");
assert.equal(urls[2], urls[1]);
await assert.rejects(fetchDrawing(source, new AbortController().signal, () => {},
  async () => new Response(JSON.stringify({ error: "Docker is stopped" }), { status: 503 })), /Docker is stopped/);
const controller = new AbortController();
let calls = 0;
await assert.rejects(fetchDrawing(source, controller.signal, () => {}, async () => {
  calls++;
  return new Response(JSON.stringify({ state: "preparing", poll: "render-jobs/one" }), { status: 202 });
}, async () => { controller.abort(); }), { name: "AbortError" });
assert.equal(calls, 1);
console.log("Drawing client: polling, phases, detailed errors, and cancellation passed");

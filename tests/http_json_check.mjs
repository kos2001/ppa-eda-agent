import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { handleJsonRequest } from "../server/http-json.mjs";

class Request extends EventEmitter {
  constructor(headers = {}) {
    super();
    this.headers = headers;
    this.resumed = false;
  }
  resume() { this.resumed = true; }
}

class Response {
  headersSent = false;
  status = null;
  body = "";
  writeHead(status) { this.status = status; this.headersSent = true; }
  end(body = "") { this.body += body; this.headersSent = true; }
}

function tick() {
  return new Promise(resolve => setImmediate(resolve));
}

{
  const req = new Request();
  const res = new Response();
  let received;
  handleJsonRequest(req, res, {}, data => { received = data; }, { maxBytes: 100 });
  const body = Buffer.from(JSON.stringify({ text: "분할 UTF-8" }));
  // Deliberately split in the middle of the first Korean character.
  const split = body.indexOf(Buffer.from("분")) + 1;
  req.emit("data", body.subarray(0, split));
  req.emit("data", body.subarray(split));
  req.emit("end");
  await tick();
  assert.deepEqual(received, { text: "분할 UTF-8" });
}

for (const invalid of [Buffer.from("{"), Buffer.from("null"), Buffer.from("[]"),
  Buffer.from([0xff])]) {
  const req = new Request();
  const res = new Response();
  let called = false;
  handleJsonRequest(req, res, {}, () => { called = true; });
  req.emit("data", invalid);
  req.emit("end");
  assert.equal(res.status, 400);
  assert.equal(called, false);
}

{
  const req = new Request();
  const res = new Response();
  let called = false;
  handleJsonRequest(req, res, {}, () => { called = true; }, { maxBytes: 4 });
  req.emit("data", Buffer.from("123"));
  req.emit("data", Buffer.from("45"));
  assert.equal(res.status, 413);
  req.emit("end");
  assert.equal(called, false);
}

{
  const req = new Request({ "content-length": "5" });
  const res = new Response();
  handleJsonRequest(req, res, {}, () => assert.fail("handler called"), { maxBytes: 4 });
  assert.equal(res.status, 413);
  assert.equal(req.resumed, true);
}

{
  const req = new Request();
  const res = new Response();
  handleJsonRequest(req, res, {}, () => { throw new Error("handler failed"); });
  req.emit("data", Buffer.from("{}"));
  req.emit("end");
  await tick();
  assert.equal(res.status, 500);
}

console.log("HTTP JSON checks passed");

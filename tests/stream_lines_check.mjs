import assert from "node:assert/strict";
import { createLineDecoder } from "../server/stream-lines.mjs";

const source = "=== candidate '한글-candidate' — overrides: {} ===\r\n"
  + "Classic - Stage 31 - Repair Design ━╸ 30/78 0:00:24\r"
  + "[ERROR] [RSZ-0090] split error\n"
  + "last line without delimiter";
const bytes = Buffer.from(source);

// Split at every byte boundary, including inside Korean and box-drawing UTF-8.
for (let split = 0; split <= bytes.length; split++) {
  const lines = [];
  const decoder = createLineDecoder(line => lines.push(line));
  decoder.write(bytes.subarray(0, split));
  decoder.write(bytes.subarray(split));
  decoder.end();
  assert.deepEqual(lines, [
    "=== candidate '한글-candidate' — overrides: {} ===",
    "Classic - Stage 31 - Repair Design ━╸ 30/78 0:00:24",
    "[ERROR] [RSZ-0090] split error",
    "last line without delimiter",
  ], `split ${split}`);
}

const emptyLines = [];
const emptyDecoder = createLineDecoder(line => emptyLines.push(line));
emptyDecoder.write(Buffer.from("a\r"));
emptyDecoder.write(Buffer.from("\nb\n\n"));
emptyDecoder.end();
assert.deepEqual(emptyLines, ["a", "b", ""]);
console.log("stream line checks passed");

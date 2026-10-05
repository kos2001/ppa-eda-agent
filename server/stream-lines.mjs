import { StringDecoder } from "node:string_decoder";

/**
 * Turn arbitrary Buffer chunks into complete lines. Both LF and CR are
 * delimiters because OpenLane redraws progress with carriage returns.
 * StringDecoder preserves a multibyte UTF-8 character split across chunks.
 */
export function createLineDecoder(onLine) {
  const decoder = new StringDecoder("utf8");
  let pending = "";

  function drain(final = false) {
    while (pending.length) {
      const match = /[\r\n]/.exec(pending);
      if (!match) break;
      const index = match.index;
      // Keep a trailing CR until the next chunk so CRLF is one delimiter.
      if (!final && pending[index] === "\r" && index === pending.length - 1) break;
      const width = pending[index] === "\r" && pending[index + 1] === "\n" ? 2 : 1;
      const line = pending.slice(0, index);
      pending = pending.slice(index + width);
      onLine(line);
    }
    if (final && pending.length) {
      onLine(pending);
      pending = "";
    }
  }

  return {
    write(chunk) {
      pending += decoder.write(chunk);
      drain();
    },
    end() {
      pending += decoder.end();
      drain(true);
    },
  };
}

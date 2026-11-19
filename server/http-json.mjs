const DEFAULT_MAX_BYTES = 8 * 1024 * 1024;

function jsonError(res, headers, status, error) {
  if (res.headersSent) return;
  res.writeHead(status, { ...headers, "Content-Type": "application/json" });
  res.end(JSON.stringify({ error }));
}

/** Read one bounded JSON request and invoke handler with the parsed value. */
export function handleJsonRequest(
  req, res, headers, handler, { maxBytes = DEFAULT_MAX_BYTES } = {}
) {
  const declared = Number(req.headers["content-length"]);
  if (Number.isFinite(declared) && declared > maxBytes) {
    jsonError(res, headers, 413, `request body exceeds ${maxBytes} bytes`);
    req.resume();
    return;
  }

  let bytes = 0;
  let rejected = false;
  const chunks = [];
  req.on("data", (chunk) => {
    if (rejected) return;
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += buffer.length;
    if (bytes > maxBytes) {
      rejected = true;
      chunks.length = 0;
      jsonError(res, headers, 413, `request body exceeds ${maxBytes} bytes`);
      return;
    }
    chunks.push(buffer);
  });
  req.on("end", () => {
    if (rejected) return;
    let data;
    try {
      const text = new TextDecoder("utf-8", { fatal: true })
        .decode(Buffer.concat(chunks));
      data = JSON.parse(text || "{}");
      if (data === null || typeof data !== "object" || Array.isArray(data)) {
        throw new TypeError("JSON object required");
      }
    } catch {
      jsonError(res, headers, 400, "request body must be valid UTF-8 JSON");
      return;
    }
    Promise.resolve().then(() => handler(data)).catch((err) => {
      console.error("[request handler error]", err);
      jsonError(res, headers, 500, String(err?.message ?? err));
    });
  });
  req.on("error", (err) => {
    if (!rejected) jsonError(res, headers, 400, String(err?.message ?? err));
  });
}

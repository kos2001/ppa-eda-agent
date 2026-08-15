// Minimal local simulation server — no framework, just node:http.
// POST /simulate {period: number} runs a real OpenSTA timing/power
// simulation (via the openroad/opensta Docker image) against the example
// design in ../sim/ and returns the raw report text. Local-only tool:
// binds 127.0.0.1, not meant to be exposed.
import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, readFile, writeFile, rm, cp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const simDir = path.resolve(__dirname, "..", "sim");
const PORT = 8123;

// Any localhost dev-server port is fine — this is a local-only tool.
const ALLOWED_ORIGIN_RE = /^http:\/\/localhost:\d+$/;

function corsHeaders(req) {
  const origin = req.headers.origin;
  if (origin && ALLOWED_ORIGIN_RE.test(origin)) {
    return {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
  }
  return {};
}

async function runSimulation(period) {
  if (typeof period !== "number" || !Number.isFinite(period) || period <= 0) {
    throw new Error("period must be a positive number (nanoseconds)");
  }

  const workDir = await mkdtemp(path.join(tmpdir(), "ppa-eda-sim-"));
  try {
    await cp(path.join(simDir, "example1.v"), path.join(workDir, "example1.v"));
    await cp(
      path.join(simDir, "nangate45_typ.lib.gz"),
      path.join(workDir, "nangate45_typ.lib.gz")
    );
    const template = await readFile(
      path.join(simDir, "run.tcl.template"),
      "utf-8"
    );
    const tcl = template.replace("{{PERIOD}}", String(period));
    await writeFile(path.join(workDir, "run.tcl"), tcl);

    const { stdout, stderr } = await execFileAsync(
      "docker",
      [
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--entrypoint",
        "/OpenSTA/build/sta",
        "-v",
        `${workDir}:/work`,
        "-w",
        "/work",
        "openroad/opensta:latest",
        "-exit",
        "run.tcl",
      ],
      { timeout: 60_000, maxBuffer: 10 * 1024 * 1024 }
    );

    if (stderr && stderr.trim()) {
      console.error("[opensta stderr]", stderr);
    }
    return stdout;
  } finally {
    await rm(workDir, { recursive: true, force: true });
  }
}

const server = createServer(async (req, res) => {
  const headers = corsHeaders(req);

  if (req.method === "OPTIONS") {
    res.writeHead(204, headers);
    res.end();
    return;
  }

  if (req.method !== "POST" || req.url !== "/simulate") {
    res.writeHead(404, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "POST /simulate {period} only" }));
    return;
  }

  let body = "";
  req.on("data", (chunk) => (body += chunk));
  req.on("end", async () => {
    try {
      const { period } = JSON.parse(body || "{}");
      const output = await runSimulation(period);
      res.writeHead(200, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ output }));
    } catch (err) {
      console.error("[simulate error]", err);
      res.writeHead(500, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(err.message ?? err) }));
    }
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`ppa-eda-agent simulation server listening on http://127.0.0.1:${PORT}`);
});

// Minimal local simulation server — no framework, just node:http.
// POST /simulate {period: number} runs a real OpenSTA timing/power
// simulation (via the openroad/opensta Docker image) against the example
// design in ../sim/ and returns the raw report text. Local-only tool:
// binds 127.0.0.1, not meant to be exposed.
import { createServer } from "node:http";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, readFile, writeFile, rm, cp, access } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const simDir = path.resolve(__dirname, "..", "sim");
const refDbDir = path.resolve(__dirname, "..", "reference-db");
const pipelineDir = path.resolve(__dirname, "..", "pipeline");
const PORT = 8123;

// Auto-loads a real .env file at the repo root, if present — same
// pattern as ~/gitspace/mi-report's load_profile(): the credential
// (PPA_EDA_GATEWAY_KEY) lives in a real .env this project never commits
// (see .gitignore) instead of having to be typed inline on every server
// start. process.loadEnvFile() is Node's own built-in .env loader
// (stable since Node 20.6) — no dependency needed. Silently no-ops if
// .env doesn't exist, so a fresh checkout still starts fine and falls
// back to the browser-paste-key flow.
try {
  process.loadEnvFile(path.resolve(__dirname, "..", ".env"));
  console.log("[env] loaded .env");
} catch {
  console.log("[env] no .env file found — server-side gateway key not configured " +
    "(browser-paste-key flow still works; see .env.example)");
}

// Any localhost dev-server port is fine — this is a local-only tool.
const ALLOWED_ORIGIN_RE = /^http:\/\/localhost:\d+$/;

function corsHeaders(req) {
  const origin = req.headers.origin;
  if (origin && ALLOWED_ORIGIN_RE.test(origin)) {
    return {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
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

// Reads the real reference-db/ case store (written by
// pipeline/orchestrator.py — see docs/superpowers/specs/
// 2026-08-21-autonomous-layout-agent-design.md) — every design's list of
// cases, each with its real iterations/candidates/verdicts/diagnosis.
// Read-only: this endpoint never runs anything, just serves what the
// pipeline already wrote to disk.
async function loadReferenceDb() {
  let index;
  try {
    index = JSON.parse(await readFile(path.join(refDbDir, "index.json"), "utf-8"));
  } catch {
    return { designs: {} };
  }

  const entries = await Promise.all(
    Object.entries(index).map(async ([designName, caseFiles]) => {
      const cases = await Promise.all(caseFiles.map(async (fileName) => {
        try {
          const raw = await readFile(path.join(refDbDir, "cases", fileName), "utf-8");
          return JSON.parse(raw);
        } catch (err) {
          console.error(`[reference-db] failed to read ${fileName}`, err);
          return null;
        }
      }));
      return [designName, cases.filter((item) => item !== null)];
    })
  );
  return { designs: Object.fromEntries(entries) };
}

// Lets the dashboard actually DRIVE the agent instead of only reading
// what it already did — this is the difference between a report viewer
// and a DTCO agent console: a real pipeline/orchestrator.py run
// (candidate generation → OpenLane → auto-repair → reference-db case),
// spawned on demand from the UI, not just past runs someone triggered
// from a terminal. In-memory only (no queue/db) since this is a
// single-operator local tool — a run's status is lost on server
// restart, same as any other in-flight local process would be.
const pipelineRuns = new Map(); // design -> {status, startedAt, finishedAt, tail, error}
const MAX_TAIL_LINES = 200;

async function designExists(design) {
  try {
    await access(path.join(pipelineDir, "designs", design, "run_spec.json"));
    return true;
  } catch {
    return false;
  }
}

function startPipelineRun(design) {
  const designDir = path.join(pipelineDir, "designs", design);
  const runSpecPath = path.join(designDir, "run_spec.json");
  const state = { status: "running", startedAt: new Date().toISOString(), finishedAt: null, tail: [], error: null };
  pipelineRuns.set(design, state);

  const proc = spawn(
    "python3",
    ["orchestrator.py", "--design", designDir, "--run-spec", runSpecPath],
    { cwd: pipelineDir }
  );

  const pushLine = (line) => {
    state.tail.push(line);
    if (state.tail.length > MAX_TAIL_LINES) state.tail.shift();
  };
  const onChunk = (chunk) => {
    for (const line of chunk.toString("utf-8").split("\n")) {
      if (line.trim()) pushLine(line);
    }
  };
  proc.stdout.on("data", onChunk);
  proc.stderr.on("data", onChunk);

  proc.on("error", (err) => {
    state.status = "error";
    state.error = `failed to start orchestrator.py: ${err.message ?? err}`;
    state.finishedAt = new Date().toISOString();
  });
  proc.on("close", (code) => {
    if (state.status === "running") {
      state.status = code === 0 ? "done" : "error";
      if (code !== 0) state.error = `orchestrator.py exited with code ${code}`;
    }
    state.finishedAt = new Date().toISOString();
  });
}

// Server-side hermes-gateway proxy — pattern borrowed from
// ~/gitspace/mi-report/backend/app/agentchat.py: the *server* holds the
// gateway credential via its own environment variable
// (PPA_EDA_GATEWAY_KEY, set in this process's own env/shell — never
// written into this codebase, never read from another project's .env by
// anything in this repo), and the browser calls this local proxy instead
// of ever handling the key itself. Falls back gracefully (GET
// /gateway-status reports false) when the env var isn't set, matching
// agentchat.py's explicit 503 rather than a silent/broken call — the
// dashboard's existing "paste your own key" flow (api/gateway.ts) still
// works standalone for anyone who'd rather not set the server env var.
const GATEWAY_BASE_URL = process.env.PPA_EDA_GATEWAY_BASE_URL || "http://127.0.0.1:8700";
const GATEWAY_MODEL = "ppa-eda-analyst";

function gatewayKey() {
  return (process.env.PPA_EDA_GATEWAY_KEY || "").trim();
}

async function proxyDiagnose(reportText, res, headers) {
  const key = gatewayKey();
  if (!key) {
    res.writeHead(503, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify({
      error: "Server-side hermes connection not configured — set " +
        "PPA_EDA_GATEWAY_KEY in this server's environment, or use the " +
        "dashboard's own client-key field instead.",
    }));
    return;
  }

  let upstream;
  try {
    upstream = await fetch(`${GATEWAY_BASE_URL}/v1/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: GATEWAY_MODEL,
        stream: true,
        messages: [
          { role: "user", content: `Diagnose this OpenSTA simulation output:\n\n${reportText}` },
        ],
      }),
    });
  } catch (err) {
    res.writeHead(502, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: `hermes-gateway connection failed: ${err.message ?? err}` }));
    return;
  }

  if (!upstream.ok || !upstream.body) {
    const text = await upstream.text().catch(() => "");
    res.writeHead(upstream.status, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: `hermes-gateway error ${upstream.status}: ${text.slice(0, 300)}` }));
    return;
  }

  // Pipe the real SSE stream straight through — same wire format the
  // dashboard's diagnoseStream() already parses, so the frontend logic
  // doesn't need two different response shapes for the two connection
  // paths (server-proxied vs. browser-direct).
  const upstreamHeader = upstream.headers.get("X-Hermes-Gateway-Upstream");
  res.writeHead(200, {
    ...headers,
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    ...(upstreamHeader ? { "X-Hermes-Gateway-Upstream": upstreamHeader } : {}),
  });
  for await (const chunk of upstream.body) {
    res.write(chunk);
  }
  res.end();
}

const server = createServer(async (req, res) => {
  const headers = corsHeaders(req);

  if (req.method === "OPTIONS") {
    res.writeHead(204, headers);
    res.end();
    return;
  }

  if (req.method === "GET" && req.url === "/reference-db") {
    try {
      const data = await loadReferenceDb();
      res.writeHead(200, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify(data));
    } catch (err) {
      console.error("[reference-db error]", err);
      res.writeHead(500, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(err.message ?? err) }));
    }
    return;
  }

  if (req.method === "POST" && req.url === "/pipeline/run") {
    let runBody = "";
    req.on("data", (chunk) => (runBody += chunk));
    req.on("end", async () => {
      try {
        const { design } = JSON.parse(runBody || "{}");
        if (typeof design !== "string" || !design.trim() || design.includes("/") || design.includes("..")) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "design (bare directory name under pipeline/designs/) required" }));
          return;
        }
        if (!(await designExists(design))) {
          res.writeHead(404, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: `no pipeline/designs/${design}/run_spec.json` }));
          return;
        }
        const existing = pipelineRuns.get(design);
        if (existing && existing.status === "running") {
          res.writeHead(409, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: `a run for ${design} is already in progress`, ...existing }));
          return;
        }
        startPipelineRun(design);
        res.writeHead(202, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ design, status: "running" }));
      } catch (err) {
        console.error("[pipeline run error]", err);
        res.writeHead(500, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: String(err.message ?? err) }));
      }
    });
    return;
  }

  if (req.method === "GET" && req.url?.startsWith("/pipeline/run-status")) {
    const design = new URL(req.url, "http://localhost").searchParams.get("design") ?? "";
    const state = pipelineRuns.get(design);
    res.writeHead(200, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify(state ?? { status: "idle" }));
    return;
  }

  if (req.method === "GET" && req.url === "/gateway-status") {
    res.writeHead(200, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify({ configured: Boolean(gatewayKey()) }));
    return;
  }

  if (req.method === "POST" && req.url === "/diagnose") {
    let diagBody = "";
    req.on("data", (chunk) => (diagBody += chunk));
    req.on("end", async () => {
      try {
        const { reportText } = JSON.parse(diagBody || "{}");
        if (typeof reportText !== "string" || !reportText.trim()) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "reportText (non-empty string) required" }));
          return;
        }
        await proxyDiagnose(reportText, res, headers);
      } catch (err) {
        console.error("[diagnose proxy error]", err);
        if (!res.headersSent) {
          res.writeHead(500, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: String(err.message ?? err) }));
        } else {
          res.end();
        }
      }
    });
    return;
  }

  if (req.method !== "POST" || req.url !== "/simulate") {
    res.writeHead(404, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify({
      error: "POST /simulate {period}, GET /reference-db, GET /gateway-status, " +
        "POST /diagnose {reportText}, POST /pipeline/run {design}, or " +
        "GET /pipeline/run-status?design=...",
    }));
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

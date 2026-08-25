// Minimal local simulation server — no framework, just node:http.
// POST /simulate {period: number} runs a real OpenSTA timing/power
// simulation (via the openroad/opensta Docker image) against the example
// design in ../sim/ and returns the raw report text. Local-only tool:
// binds 127.0.0.1, not meant to be exposed.
import { createServer } from "node:http";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, readFile, writeFile, rm, cp, access, stat } from "node:fs/promises";
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
// Per-file cache keyed by mtimeMs — reference-db/cases/*.json only grows
// (self_improve.py, the dashboard's own trigger, and manual orchestrator
// runs all append to it over time; the sram_wrapper case alone is
// already 13KB just for one diagnosis) and GET /reference-db gets
// re-polled on every dashboard load plus after every triggered pipeline
// run finishes. Re-reading and re-JSON.parsing every case file on every
// request is pure repeated work when most files haven't changed since
// the last request — this cache does the disk stat (cheap) every time
// but only re-reads+re-parses a file when its mtime actually moved.
const caseFileCache = new Map(); // fileName -> {mtimeMs, data}

async function readCaseFileCached(fileName) {
  const filePath = path.join(refDbDir, "cases", fileName);
  const { mtimeMs } = await stat(filePath);
  const cached = caseFileCache.get(fileName);
  if (cached && cached.mtimeMs === mtimeMs) return cached.data;
  const raw = await readFile(filePath, "utf-8");
  const data = JSON.parse(raw);
  caseFileCache.set(fileName, { mtimeMs, data });
  return data;
}

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
          return await readCaseFileCached(fileName);
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

// One definition of "a safe design name", used by every endpoint that
// turns one into a filesystem path or a subprocess argument. Previously
// this test was inline in a single route; the review endpoints need the
// identical rule, and two copies of a security check is one too many.
function isSafeDesignName(design) {
  return typeof design === "string" && design.trim() !== ""
    && !design.includes("/") && !design.includes("..");
}


async function designExists(design) {
  try {
    await access(path.join(pipelineDir, "designs", design, "run_spec.json"));
    return true;
  } catch {
    return false;
  }
}

function startPipelineRun(design, { maxIterations = null } = {}) {
  const designDir = path.join(pipelineDir, "designs", design);
  const runSpecPath = path.join(designDir, "run_spec.json");
  const state = { status: "running", startedAt: new Date().toISOString(), finishedAt: null, tail: [], error: null, maxIterations };
  pipelineRuns.set(design, state);

  const args = ["orchestrator.py", "--design", designDir, "--run-spec", runSpecPath];
  // A run that stopped at max_iterations_reached needs exactly one thing:
  // more budget. Without this the console could only re-run it with the
  // same budget that already proved insufficient, so the suggested
  // action would have been decorative.
  if (Number.isInteger(maxIterations) && maxIterations > 0) {
    args.push("--max-iterations", String(maxIterations));
  }
  const proc = spawn("python3", args, { cwd: pipelineDir });

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

// A second, optional route for work that needs a language model but not
// a chip-PPA analyst. Measured why this exists: every hermes-gateway
// model is somebody's persona, and ppa-eda-analyst spends ~19,100 prompt
// tokens before it reads a single character of input — a two-letter
// prompt still bills 19,129, and a 17-character translation still took
// 9.2s. Worse, the persona reasons rather than just answering:
// translating the real 8,744-char sram_wrapper diagnosis produced 9,843
// completion tokens for roughly 3,500 tokens of actual Korean, so about
// two thirds of a ~200s wait was the analyst thinking about EDA.
//
// Translation needs none of that. Pointing it at a plain model removes
// the persona prompt and the reasoning it triggers. Borrowed from
// ~/gitspace/lsi_error_analyzer, which drives OpenRouter directly (via
// Agno) rather than through a persona gateway — the direct-LLM *route*
// is the transferable part; Agno itself is a Python framework and this
// is a Node server that already speaks the identical OpenAI-compatible
// wire format, so adopting it would add a dependency to gain nothing.
//
// Entirely opt-in. With no key set, everything keeps using the gateway
// exactly as before, so a fresh checkout behaves identically.
const DIRECT_LLM_BASE_URL =
  process.env.PPA_EDA_DIRECT_LLM_BASE_URL || "https://openrouter.ai/api/v1";
const DIRECT_LLM_MODEL = process.env.PPA_EDA_DIRECT_LLM_MODEL || "";

function directLlmKey() {
  return (process.env.PPA_EDA_DIRECT_LLM_KEY || "").trim();
}

function directLlmAvailable() {
  return Boolean(directLlmKey() && DIRECT_LLM_MODEL);
}

// `preferDirect` asks for the plain-model route when one is configured.
// It is a request, not a guarantee: unconfigured falls back to the
// gateway rather than failing, because a slower answer beats no answer.
async function proxyChat(prompt, res, headers, { preferDirect = false } = {}) {
  const useDirect = preferDirect && directLlmAvailable();
  const key = useDirect ? directLlmKey() : gatewayKey();
  if (!key) {
    res.writeHead(503, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify({
      error: "Server-side hermes connection not configured — set " +
        "PPA_EDA_GATEWAY_KEY in this server's environment, or use the " +
        "dashboard's own client-key field instead.",
    }));
    return;
  }

  const baseUrl = useDirect ? DIRECT_LLM_BASE_URL : GATEWAY_BASE_URL;
  const model = useDirect ? DIRECT_LLM_MODEL : GATEWAY_MODEL;

  let upstream;
  try {
    upstream = await fetch(`${baseUrl}/v1/chat/completions`.replace("/v1/v1/", "/v1/"), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model,
        stream: true,
        messages: [{ role: "user", content: prompt }],
      }),
    });
  } catch (err) {
    const which = useDirect ? "direct LLM" : "hermes-gateway";
    res.writeHead(502, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: `${which} connection failed: ${err.message ?? err}` }));
    return;
  }

  if (!upstream.ok || !upstream.body) {
    const text = await upstream.text().catch(() => "");
    res.writeHead(upstream.status, { ...headers, "Content-Type": "application/json" });
    res.end(JSON.stringify({
      error: `${useDirect ? "direct LLM" : "hermes-gateway"} error `
        + `${upstream.status}: ${text.slice(0, 300)}`,
    }));
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

  // Serves the real rendered layout PNGs orchestrator.py stored under
  // reference-db/layouts/ (see its capture_layout_image()). Read-only,
  // and path-constrained to that one directory: the filename comes from
  // a case JSON, but this endpoint is reachable directly, so it must
  // not be usable to read arbitrary files.
  if (req.method === "GET" && req.url?.startsWith("/reference-db/layouts/")) {
    const name = decodeURIComponent(req.url.slice("/reference-db/layouts/".length));
    if (!/^[A-Za-z0-9_.-]+\.png$/.test(name)) {
      res.writeHead(400, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "invalid layout image name" }));
      return;
    }
    try {
      const png = await readFile(path.join(refDbDir, "layouts", name));
      res.writeHead(200, { ...headers, "Content-Type": "image/png" });
      res.end(png);
    } catch {
      res.writeHead(404, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: `no layout image ${name}` }));
    }
    return;
  }

  // Stage 8's human-in-the-loop, carried out here instead of dropping
  // the operator into a terminal. Until this existed the console could
  // trigger and watch the pipeline but could not carry it through the
  // one step that needs judgment: at escalation it printed a shell
  // command and stopped being part of the process. Three endpoints, one
  // per real step of pipeline/request_review.py's own workflow —
  // generate the request, get a review, apply it back into the case.
  if (req.method === "POST" && req.url === "/review/request") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      try {
        const { design } = JSON.parse(body || "{}");
        if (!isSafeDesignName(design)) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "design (bare directory name) required" }));
          return;
        }
        const { stdout } = await execFileAsync(
          "python3", ["request_review.py", "request", "--design", design],
          { cwd: pipelineDir, timeout: 60_000, maxBuffer: 4 * 1024 * 1024 }
        );
        // request_review.py prints the path it wrote; return the real
        // file's content so the console can show the operator exactly
        // what a reviewer would be given.
        const written = stdout.trim().split(/\s+/).pop();
        const content = await readFile(written, "utf-8").catch(() => null);
        res.writeHead(200, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ file: written, content }));
      } catch (err) {
        console.error("[review request error]", err);
        res.writeHead(500, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: String(err.stderr || err.message || err) }));
      }
    });
    return;
  }

  // Streams a real review from hermes-gateway for a generated request.
  // Same SSE path the diagnosis and translation features already use, so
  // the console gets a second opinion without the operator leaving it.
  if (req.method === "POST" && req.url === "/review/ask") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      try {
        const { requestText } = JSON.parse(body || "{}");
        if (typeof requestText !== "string" || !requestText.trim()) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "requestText (non-empty string) required" }));
          return;
        }
        await proxyChat(
          "You are reviewing a stuck chip-layout run. Below is the real " +
            "review request generated by the pipeline, including the run's " +
            "actual diagnosis and error output. Give a specific verdict: is " +
            "there an actionable next candidate configuration to try, or " +
            "should this stay open? Cite only evidence present below — if " +
            "something cannot be determined from it, say so rather than " +
            "assuming.\n\n" + requestText,
          res, headers
        );
      } catch (err) {
        console.error("[review ask error]", err);
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

  if (req.method === "POST" && req.url === "/review/apply") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      try {
        const { design, agent, responseText } = JSON.parse(body || "{}");
        if (!isSafeDesignName(design) || typeof agent !== "string" || !agent.trim()
            || typeof responseText !== "string" || !responseText.trim()) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "design, agent and responseText required" }));
          return;
        }
        // request_review.py takes the response as a file so the text
        // never rides on a shell command line — the same reason that
        // script exists at all (a backtick in review prose once ate part
        // of a diagnosis through shell interpolation).
        const tmp = path.join(tmpdir(), `ppa-review-${Date.now()}.txt`);
        await writeFile(tmp, responseText, "utf-8");
        try {
          await execFileAsync("python3",
            ["request_review.py", "apply", "--design", design,
             "--agent", agent, "--response-file", tmp],
            { cwd: pipelineDir, timeout: 60_000 });
        } finally {
          await rm(tmp, { force: true });
        }
        res.writeHead(200, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ applied: true, design, agent }));
      } catch (err) {
        console.error("[review apply error]", err);
        res.writeHead(500, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: String(err.stderr || err.message || err) }));
      }
    });
    return;
  }

  if (req.method === "POST" && req.url === "/pipeline/run") {
    let runBody = "";
    req.on("data", (chunk) => (runBody += chunk));
    req.on("end", async () => {
      try {
        const { design, maxIterations } = JSON.parse(runBody || "{}");
        if (!isSafeDesignName(design)) {
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
        startPipelineRun(design, { maxIterations });
        res.writeHead(202, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ design, status: "running", maxIterations: maxIterations ?? null }));
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
    res.end(JSON.stringify({
      configured: Boolean(gatewayKey()),
      // Whether a plain-model route is configured for tasks that don't
      // need the analyst persona (see proxyChat's DIRECT_LLM_* notes).
      directLlm: directLlmAvailable() ? DIRECT_LLM_MODEL : null,
    }));
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
        await proxyChat(`Diagnose this OpenSTA simulation output:\n\n${reportText}`, res, headers);
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

  // On-demand machine translation for real reference-db content
  // (pipeline diagnosis text, human-in-the-loop review summaries) — the
  // dashboard's i18n only covers UI chrome, never this data, since it's
  // real subagent-written evidence with precise numbers
  // (transition times, capacitances) that must stay exactly as written
  // in its original language. Translation is opt-in, on-demand, and the
  // dashboard labels it as machine-translated rather than silently
  // replacing the original — see docs/superpowers/specs/
  // 2026-08-21-autonomous-layout-agent-design.md.
  if (req.method === "POST" && req.url === "/translate") {
    let translateBody = "";
    req.on("data", (chunk) => (translateBody += chunk));
    req.on("end", async () => {
      try {
        const { text } = JSON.parse(translateBody || "{}");
        if (typeof text !== "string" || !text.trim()) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "text (non-empty string) required" }));
          return;
        }
        await proxyChat(
          "Translate the following semiconductor design (EDA/OpenLane/timing) " +
            "diagnosis text into Korean. Preserve every number, unit, signal " +
            "name, file path, and technical term (e.g. keep 'PDN-0185', " +
            "'FP_CORE_UTIL', 'RSZ-0090' as-is) exactly as written — do not " +
            "round, re-derive, or omit any figure. Output only the " +
            "translation, no commentary:\n\n" + text,
          res,
          headers,
          // Translation is the clearest case for the plain-model route:
          // it needs a language model, not a chip-PPA analyst.
          { preferDirect: true }
        );
      } catch (err) {
        console.error("[translate proxy error]", err);
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
        "POST /diagnose {reportText}, POST /translate {text}, " +
        "POST /pipeline/run {design}, or GET /pipeline/run-status?design=...",
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

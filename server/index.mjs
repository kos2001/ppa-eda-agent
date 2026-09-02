// Minimal local simulation server — no framework, just node:http.
// POST /simulate {period: number} runs a real OpenSTA timing/power
// simulation (via the openroad/opensta Docker image) against the example
// design in ../sim/ and returns the raw report text. Local-only tool:
// binds 127.0.0.1, not meant to be exposed.
import { createServer } from "node:http";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { appendFile, mkdir, mkdtemp, readFile, readdir, writeFile, rm, cp, access, stat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const simDir = path.resolve(__dirname, "..", "sim");
const refDbDir = path.resolve(__dirname, "..", "reference-db");
const pipelineDir = path.resolve(__dirname, "..", "pipeline");
const feedbackFile = path.join(refDbDir, "feedback.jsonl");
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

// Where an AI review draft is kept between asking for it and using it.
//
// Asking costs a real model call over a document that is thousands of
// words long — the aes request took over two minutes — and the panel
// threw the answer away on every re-render, tab switch and reload. A
// person who wanted to read the draft, think, and then write their own
// verdict had to pay for it again each time they came back.
//
// Keyed by a hash of the request document itself, not by the design
// name. The request is regenerated from the design's latest case, so
// when the case changes the document changes, the key changes, and the
// stale draft is simply never found again. Nothing has to remember to
// invalidate it.
//
// On disk rather than in memory so it survives a server restart, and
// gitignored because it is a model's draft: if it is worth keeping it
// gets applied into the case, and if it was not applied it is not
// evidence of anything.
const REVIEW_CACHE_DIR = path.resolve(__dirname, "..", ".cache", "reviews");

function reviewCacheKey(requestText, lang) {
  return createHash("sha256")
    .update(`${lang ?? "en"}\u0000${requestText}`)
    .digest("hex")
    .slice(0, 32);
}

async function writeReviewDraft(design, requestText, lang, text) {
  if (!text.trim()) return;
  await mkdir(REVIEW_CACHE_DIR, { recursive: true });
  const payload = {
    design,
    lang: lang ?? "en",
    key: reviewCacheKey(requestText, lang),
    written_at: new Date().toISOString(),
    text,
  };
  await writeFile(path.join(REVIEW_CACHE_DIR, `${design}.json`),
                  JSON.stringify(payload, null, 2));
}

/** The cached draft for this design, or nulls if there is none.
 *
 * Returns the key it was stored under so the caller can tell whether it
 * still matches the request it is looking at — a draft written for an
 * older case is not an answer to the current one.
 */
async function readReviewDraft(design) {
  try {
    const raw = await readFile(
      path.join(REVIEW_CACHE_DIR, `${design}.json`), "utf-8");
    const draft = JSON.parse(raw);
    return { text: draft.text, key: draft.key,
             lang: draft.lang, written_at: draft.written_at };
  } catch {
    return { text: null, key: null, lang: null, written_at: null };
  }
}

async function readCaseFileCached(fileName) {
  const filePath = path.join(refDbDir, "cases", fileName);
  const { mtimeMs } = await stat(filePath);
  const cached = caseFileCache.get(fileName);
  if (cached && cached.mtimeMs === mtimeMs) return cached.data;
  const raw = await readFile(filePath, "utf-8");
  // The name is attached here rather than left for the caller because
  // it is the only thing that identifies a case. A case records `date`,
  // and the store keeps one dated file per design per day plus a
  // timestamped file for every re-run that day, so `date` is shared by
  // 41 of the 54 cases currently on disk. Everything downstream that
  // has to order cases in time, or key a list by them, reads the time
  // out of this name.
  const data = { ...JSON.parse(raw), file: fileName };
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

// Turning the run's own output into live per-step progress.
//
// The console could start a run and show a scrolling tail of it, which
// answers "is it alive" and nothing else. A full flow is 78 steps and
// several minutes; from a tail you cannot tell whether a candidate is at
// floorplan or at signoff, which candidate of nine is running, or where
// the last one died. All of that is already in the stream:
//
//   === candidate 'sweep-util-25' — overrides: {...} ===
//   Classic - Stage 31 - Repair Design (Post-Global Placement) ━╸ 30/78 0:00:24
//   [ERROR] [RSZ-0090] Max transition time from SDC is 0.040ns...
//
// so this parses it rather than adding a second channel that could
// disagree with the log. The progress bar OpenLane draws uses a carriage
// return and box-drawing characters; only the numbers are taken.
const CANDIDATE_LINE = /^=== candidate '([^']+)'/;
const STAGE_LINE = /(\w+) - Stage (\d+) - (.+?)\s+[─-▟\s]*?(\d+)\/(\d+)\s+(\d+:\d{2}:\d{2})/;
const ERROR_LINE = /\[ERROR\]\s*(.+?)\s*$/;

function trackProgress(state, rawLine) {
  // Strip ANSI colour so the regexes match what a person sees.
  const line = rawLine.replace(/\x1b\[[0-9;]*m/g, "");

  const cand = line.match(CANDIDATE_LINE);
  if (cand) {
    // A new candidate starting means the previous one is finished; if it
    // never reported an error it got all the way through.
    const prev = state.progress?.at(-1);
    if (prev && prev.status === "running") prev.status = "done";
    (state.progress ??= []).push({
      tag: cand[1], step: 0, total: null, stepName: null,
      elapsed: null, status: "running", error: null,
      startedAt: new Date().toISOString(),
    });
    return;
  }

  const current = state.progress?.at(-1);
  if (!current) return;

  // The progress bar only reaches us when the run is over.
  //
  // OpenLane draws it with Rich, which checks isatty() and disables the
  // live bar entirely when its output is a pipe — so a piped run emits
  // exactly one "Stage" line, at the end, no matter how it is split.
  // Verified twice: from the server's own stream and from a plain shell
  // redirect, both showed a single line at 78/78.
  //
  // So this parses it only for the finishing totals, and live progress
  // comes from watching the run directory instead (pollRunDir below):
  // OpenLane creates one NN-tool-step/ directory per step as it goes,
  // which is ground truth and observable while it happens.
  const stage = line.match(STAGE_LINE);
  if (stage) {
    state.expectedSteps = Number(stage[5]) || state.expectedSteps || null;
    current.flow = stage[1];
    current.total = Number(stage[5]);
    current.elapsed = stage[6];
    // Only ever forward. When a run ends, its buffered output arrives at
    // once and the last Stage line parsed is not necessarily the highest
    // — observed a run finish at "62/78 KLayout vs. Magic XOR" after the
    // directory poll had already seen step 74. A progress bar that jumps
    // backwards at the finish line is a visible wrong, and within one
    // candidate the step count only rises.
    const n = Number(stage[4]);
    if (n >= current.step) {
      current.step = n;
      current.stepName = stage[3].trim();
    }
    return;
  }

  const err = line.match(ERROR_LINE);
  // Keep the first error, not the last: OpenLane repeats the failure in
  // its summary, and the first occurrence is the one with the context.
  if (err && !current.error) {
    current.error = err[1].slice(0, 300);
    current.status = "failed";
  }
}

// Live progress from the run directory.
//
// OpenLane materialises one directory per step — 13-openroad-floorplan,
// 31-openroad-repairdesignpostgpl — as it reaches them, so the highest
// number present is where the run is now. Unlike the progress bar this
// survives being piped, because it is on disk rather than on a terminal.
//
// The total is not invented: it comes from a previous completed run of
// the same design if there is one, and is left null otherwise, so the
// view shows "step 31" rather than a denominator nobody measured.
const STEP_DIR = /^(\d+)-(.+)$/;

async function pollRunDir(design, state) {
  const current = state.progress?.at(-1);
  if (!current || current.status !== "running") return;
  const runDir = path.join(pipelineDir, "designs", design, "runs", current.tag);
  let entries;
  try {
    entries = await readdir(runDir, { withFileTypes: true });
  } catch {
    return; // not created yet
  }
  let best = null;
  for (const e of entries) {
    if (!e.isDirectory()) continue;
    const m = e.name.match(STEP_DIR);
    if (!m) continue;
    const n = Number(m[1]);
    if (!best || n > best.n) best = { n, name: m[2] };
  }
  if (!best || best.n < current.step) return;
  current.step = best.n;
  current.stepName = best.name.replace(/^[a-z]+-/, "").replace(/-/g, " ");
  current.total = state.expectedSteps ?? null;
  const started = Date.parse(current.startedAt);
  if (Number.isFinite(started)) {
    const secs = Math.max(0, Math.round((Date.now() - started) / 1000));
    current.elapsed = `0:${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;
  }
}

// The last candidate has no successor to close it out, so a finished run
// would otherwise show its final candidate stuck at "running" forever.
function finishProgress(state) {
  const last = state.progress?.at(-1);
  if (last && last.status === "running") {
    last.status = state.status === "error" ? "failed" : "done";
  }
}

function startPipelineRun(design, { maxIterations = null } = {}) {
  const designDir = path.join(pipelineDir, "designs", design);
  const runSpecPath = path.join(designDir, "run_spec.json");
  // Carry forward the step count a previous run of this design actually
  // reached, so the bar has a denominator that was observed rather than
  // assumed. The first run of a design shows "step 31" with no total —
  // honest about not knowing yet.
  const previous = pipelineRuns.get(design);
  const state = {
    status: "running", startedAt: new Date().toISOString(), finishedAt: null,
    tail: [], progress: [], error: null, maxIterations,
    expectedSteps: previous?.expectedSteps ?? null,
  };
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
    // Split on carriage returns as well as newlines. OpenLane redraws
    // its progress bar in place with \r and only emits \n when the bar
    // is finished, so splitting on \n alone accumulates every
    // intermediate update into one enormous line and the live view sees
    // exactly one step — the last. Measured: a full 78-step run produced
    // a single parseable "Stage" line, at 78/78.
    for (const line of chunk.toString("utf-8").split(/\r\n|\r|\n/)) {
      if (!line.trim()) continue;
      trackProgress(state, line);
      // Progress-bar redraws are the point of the above and pure noise
      // in a readable tail — hundreds of near-identical lines would
      // evict everything else from a 200-line buffer.
      if (!STAGE_LINE.test(line)) pushLine(line);
    }
  };
  proc.stdout.on("data", onChunk);
  proc.stderr.on("data", onChunk);

  // Poll the run directory while the process lives. 1.5 s is well under
  // the time any OpenLane step takes, and it is a directory listing.
  const dirTimer = setInterval(() => {
    pollRunDir(design, state).catch(() => {});
  }, 1500);

  proc.on("error", (err) => {
    state.status = "error";
    state.error = `failed to start orchestrator.py: ${err.message ?? err}`;
    state.finishedAt = new Date().toISOString();
    clearInterval(dirTimer);
    finishProgress(state);
  });
  proc.on("close", (code) => {
    if (state.status === "running") {
      state.status = code === 0 ? "done" : "error";
      if (code !== 0) state.error = `orchestrator.py exited with code ${code}`;
    }
    state.finishedAt = new Date().toISOString();
    clearInterval(dirTimer);
    finishProgress(state);
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
// The hermes-agent profile this repo actually ships now — see
// server/hermes-gateway.mjs — was "ppa-eda-analyst" while the gateway
// was a documented but unbuilt aspiration; renamed to match the real
// profile name once one existed.
const GATEWAY_MODEL = process.env.PPA_EDA_GATEWAY_MODEL || "ppa-agent";

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
// Language instruction appended to prompts that generate *new* agent
// output.
//
// The dashboard's language toggle used to switch UI chrome only: the
// agent answered in English whichever language was selected, and the
// user then pressed "translate" to read it. Asking the model to write in
// the target language instead removes a step and a source of drift — a
// diagnosis written in Korean is not a translation of an English one.
//
// Deliberately does NOT apply to stored reference-db evidence. That text
// is a record of what real subagents found, and silently re-rendering a
// measured transition time or capacitance through a model would
// misrepresent a finding; it keeps its explicit machine-translation
// button, labelled as such.
//
// The instruction is explicit about technical tokens because a model
// asked for Korean will otherwise localise metric names and error codes
// (RSZ-0090, timing__setup__ws) that only mean anything untranslated.
const _KEEP_IDENTIFIERS =
  " Keep every number, unit, signal name, file path, metric key, tool " +
  "name and error code exactly as written — translate the prose around " +
  "them, never the identifiers.";

// Both languages are stated explicitly. English was originally left
// blank on the assumption that it was the model's default; measuring it
// showed otherwise — with no instruction at all this gateway's model
// answers in Korean, so selecting English would have changed nothing.
const LANGUAGE_INSTRUCTION = {
  ko: "\n\nRespond in Korean (한국어)." + _KEEP_IDENTIFIERS,
  en: "\n\nRespond in English." + _KEEP_IDENTIFIERS,
};

function withLanguage(prompt, lang) {
  // Unknown or absent lang falls back to English rather than to no
  // instruction, since "no instruction" is not neutral here.
  return prompt + (LANGUAGE_INSTRUCTION[lang] ?? LANGUAGE_INSTRUCTION.en);
}

async function proxyChat(prompt, res, headers,
                        { preferDirect = false, onComplete = null } = {}) {
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
  // Decoded alongside the passthrough only when someone asked for the
  // finished text — the stream itself is still forwarded byte for byte,
  // so nothing about the response changes when a caller wants a copy.
  const decoder = onComplete ? new TextDecoder() : null;
  let raw = "";
  for await (const chunk of upstream.body) {
    res.write(chunk);
    if (decoder) raw += decoder.decode(chunk, { stream: true });
  }
  res.end();

  // After res.end(), so a slow consumer of the finished text cannot
  // hold the response open. Errors here are logged and dropped: failing
  // to cache a draft must not fail the request that produced it.
  if (onComplete) {
    try {
      await onComplete(collectSseText(raw));
    } catch (err) {
      console.error("[proxyChat onComplete]", err);
    }
  }
}

/** The assistant text out of a server-sent-event stream. */
function collectSseText(raw) {
  const out = [];
  for (const line of raw.split("\n")) {
    if (!line.startsWith("data: ")) continue;
    const payload = line.slice(6).trim();
    if (!payload || payload === "[DONE]") continue;
    try {
      const delta = JSON.parse(payload).choices?.[0]?.delta?.content;
      if (delta) out.push(delta);
    } catch {
      // A partial frame at a chunk boundary is normal mid-stream and
      // there is nothing to recover from it here.
    }
  }
  return out.join("");
}

const server = createServer(async (req, res) => {
  const headers = corsHeaders(req);

  if (req.method === "OPTIONS") {
    res.writeHead(204, headers);
    res.end();
    return;
  }

  // The TCL the Simulate tab is about to run, read from the same file
  // runSimulation() fills in. Served rather than copied into the
  // dashboard: a page that claims to run a real tool has to be able to
  // show what it ran, and a second copy of these five lines would
  // eventually show something the server no longer runs.
  if (req.method === "GET" && req.url === "/simulate/script") {
    try {
      const template = await readFile(
        path.join(simDir, "run.tcl.template"), "utf-8");
      res.writeHead(200, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ template }));
    } catch (err) {
      res.writeHead(500, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(err.message ?? err) }));
    }
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
  // The AI draft this design's current review request already produced,
  // if one has been produced. Served on its own so the panel can show it
  // the moment the request is generated, rather than making a person ask
  // for something that already exists.
  if (req.method === "POST" && req.url === "/review/cached") {
    let cachedBody = "";
    req.on("data", (c) => (cachedBody += c));
    req.on("end", async () => {
      try {
        const { design, requestText, lang } = JSON.parse(cachedBody || "{}");
        if (!isSafeDesignName(design) || typeof requestText !== "string") {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "design and requestText required" }));
          return;
        }
        const draft = await readReviewDraft(design);
        // The key is compared here rather than handed to the caller to
        // compare. It was returned for the caller to check and the
        // caller did not, so a draft written for a different request —
        // a different case, or the same one asked in another language —
        // was put in the box as if it answered this one. Verified
        // against a real miss: an English draft appeared under a Korean
        // request. A cache that answers the wrong question is worse
        // than no cache, because the wrong answer looks like an answer.
        const fresh = draft.text
          && draft.key === reviewCacheKey(requestText, lang);
        res.writeHead(200, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify(fresh
          ? { text: draft.text, written_at: draft.written_at }
          : { text: null, written_at: null }));
      } catch (err) {
        console.error("[review cached error]", err);
        res.writeHead(500, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: String(err.message ?? err) }));
      }
    });
    return;
  }

  if (req.method === "POST" && req.url === "/review/ask") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      try {
        const { requestText, lang, design, refresh } = JSON.parse(body || "{}");
        if (typeof requestText !== "string" || !requestText.trim()) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "requestText (non-empty string) required" }));
          return;
        }

        // A draft already written for this exact request is served
        // instead of paying for it again. Replayed as SSE so the client
        // has one code path for both, with a header saying which it got
        // — a cached draft that pretended to be fresh would hide the
        // fact that nobody asked the model just now.
        const wantsCache = isSafeDesignName(design) && !refresh;
        if (wantsCache) {
          const cached = await readReviewDraft(design);
          if (cached.text && cached.key === reviewCacheKey(requestText, lang)) {
            res.writeHead(200, {
              ...headers,
              "Content-Type": "text/event-stream",
              "X-Review-Cache": "hit",
              "X-Review-Cached-At": cached.written_at ?? "",
            });
            res.write(`data: ${JSON.stringify({
              choices: [{ delta: { content: cached.text } }],
            })}\n\n`);
            res.write("data: [DONE]\n\n");
            res.end();
            return;
          }
        }

        // Written only on a complete answer. A draft cut off by a
        // dropped connection is worse than no draft: it would be served
        // back as if it were the model's verdict.
        const capture = isSafeDesignName(design)
          ? (text) => writeReviewDraft(design, requestText, lang, text)
              .catch((e) => console.error("[review cache write]", e))
          : null;

        await proxyChat(
          withLanguage(
          "You are reviewing a stuck chip-layout run. Below is the real " +
            "review request generated by the pipeline, including the run's " +
            "actual diagnosis and error output. Give a specific verdict: is " +
            "there an actionable next candidate configuration to try, or " +
            "should this stay open? Cite only evidence present below — if " +
            "something cannot be determined from it, say so rather than " +
            "assuming.\n\n" + requestText, lang),
          res, { ...headers, "X-Review-Cache": "miss" }, { onComplete: capture }
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

  // Operator feedback, appended to a real file.
  //
  // Everything else in this console records what the *tools* said. This
  // records what the person using it said, which is the one kind of
  // evidence the pipeline has never collected — and the manual page is
  // the natural place to ask, because a reader who had to look
  // something up has just discovered a gap.
  //
  // JSONL rather than JSON: entries only ever get appended, and an
  // append cannot corrupt what is already there the way a rewrite of a
  // whole array can. Stored beside the cases so it is backed up and
  // versioned with them.
  if (req.method === "POST" && req.url === "/feedback") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      try {
        const { message, kind, page } = JSON.parse(body || "{}");
        if (typeof message !== "string" || !message.trim()) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "message (non-empty string) required" }));
          return;
        }
        const entry = {
          at: new Date().toISOString(),
          // Free text is the point; the rest is optional context so a
          // reader can tell a bug report from a feature request without
          // the writer having to say so twice.
          kind: typeof kind === "string" ? kind.slice(0, 40) : "note",
          page: typeof page === "string" ? page.slice(0, 60) : null,
          message: message.slice(0, 4000),
        };
        await appendFile(feedbackFile, JSON.stringify(entry) + "\n", "utf-8");
        res.writeHead(200, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true, at: entry.at }));
      } catch (err) {
        console.error("[feedback error]", err);
        res.writeHead(500, { ...headers, "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: String(err.message ?? err) }));
      }
    });
    return;
  }

  if (req.method === "GET" && req.url === "/feedback") {
    try {
      const raw = await readFile(feedbackFile, "utf-8").catch(() => "");
      // One bad line must not hide every good one, so parse per line.
      const entries = raw
        .split("\n")
        .filter((line) => line.trim())
        .map((line) => {
          try {
            return JSON.parse(line);
          } catch {
            return null;
          }
        })
        .filter(Boolean)
        .reverse();
      res.writeHead(200, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ entries, file: "reference-db/feedback.jsonl" }));
    } catch (err) {
      res.writeHead(500, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(err.message ?? err) }));
    }
    return;
  }

  // Where to improve next, as data rather than terminal output.
  //
  // self_improve.py has always computed this — auto-repair coverage,
  // review backlog, whether the case store can support a surrogate yet,
  // what retrieval can find precedent for — and printed it. The console
  // showed individual cases and never the state of the system that
  // produces them.
  if (req.method === "GET" && req.url === "/self-improve") {
    try {
      const { stdout } = await execFileAsync(
        "python3",
        ["-c",
         "import sys, json; sys.path.insert(0, '.'); import self_improve; " +
         "print(json.dumps(self_improve.scan_all()))"],
        { cwd: pipelineDir, timeout: 120_000, maxBuffer: 32 * 1024 * 1024 }
      );
      res.writeHead(200, { ...headers, "Content-Type": "application/json" });
      res.end(stdout);
    } catch (err) {
      console.error("[self-improve error]", err);
      res.writeHead(500, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(err.message ?? err) }));
    }
    return;
  }

  // Where the data comes from and who reads it. SystemHealth answers
  // "does anything need attention"; this answers "what is in here and
  // where does it go", which had no answer anywhere in the console.
  if (req.method === "GET" && req.url === "/data-lineage") {
    try {
      const { stdout } = await execFileAsync(
        "python3",
        ["-c",
         "import sys, json; sys.path.insert(0, '.'); import data_lineage; " +
         "print(json.dumps(data_lineage.report(), default=str))"],
        { cwd: pipelineDir, timeout: 180_000, maxBuffer: 32 * 1024 * 1024 }
      );
      res.writeHead(200, { ...headers, "Content-Type": "application/json" });
      res.end(stdout);
    } catch (err) {
      console.error("[data-lineage error]", err);
      res.writeHead(500, { ...headers, "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(err.message ?? err) }));
    }
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

  // Free-form questions about this service, grounded in what the repo
  // holds — documents, module reasoning, and the live case store (see
  // pipeline/service_qa.py).
  //
  // TWO ENDPOINTS ON PURPOSE. /ask/sources retrieves and returns the
  // passages; /ask writes an answer from them. The split is what makes
  // the feature work with no hermes-gateway key: the client always shows
  // the sources, and only asks for prose when a model is reachable. One
  // endpoint that did both would be a 503 and a blank box on exactly the
  // checkout that has not configured a key yet.
  if (req.method === "POST" && (req.url === "/ask" || req.url === "/ask/sources")) {
    const wantsAnswer = req.url === "/ask";
    let askBody = "";
    req.on("data", (chunk) => (askBody += chunk));
    req.on("end", async () => {
      try {
        const { question } = JSON.parse(askBody || "{}");
        if (typeof question !== "string" || !question.trim()) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "question (non-empty string) required" }));
          return;
        }
        const { stdout } = await execFileAsync(
          "python3",
          ["-c",
           "import sys, json; sys.path.insert(0, '.'); import service_qa; " +
           "print(json.dumps(service_qa.build_prompt(sys.argv[1])))",
           question],
          { cwd: pipelineDir, timeout: 60_000, maxBuffer: 32 * 1024 * 1024 }
        );
        const built = JSON.parse(stdout);

        if (!wantsAnswer) {
          res.writeHead(200, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ sources: built.sources, facts: built.facts }));
          return;
        }
        // Nothing was retrieved, so there is nothing to be grounded in.
        // Asking the model anyway is asking it to invent, which is the
        // one failure this whole path exists to prevent.
        if (!built.prompt) {
          res.writeHead(200, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ sources: [], facts: null, grounded: false }));
          return;
        }
        // preferDirect: this is a plain question-answering task, not the
        // ppa-eda-analyst persona the diagnosis route needs.
        await proxyChat(built.prompt, res, headers, { preferDirect: true });
      } catch (err) {
        console.error("[ask error]", err);
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

  if (req.method === "POST" && req.url === "/diagnose") {
    let diagBody = "";
    req.on("data", (chunk) => (diagBody += chunk));
    req.on("end", async () => {
      try {
        const { reportText, lang } = JSON.parse(diagBody || "{}");
        if (typeof reportText !== "string" || !reportText.trim()) {
          res.writeHead(400, { ...headers, "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "reportText (non-empty string) required" }));
          return;
        }
        await proxyChat(
          withLanguage(`Diagnose this OpenSTA simulation output:\n\n${reportText}`, lang),
          res, headers);
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

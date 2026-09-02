// A real hermes-gateway: an OpenAI-compatible /v1/chat/completions server
// backed by the actual Hermes Agent CLI (github.com/NousResearch/hermes-agent,
// installed locally via Hermes Desktop) running the "ppa-agent" profile.
//
// server/index.mjs's proxyChat() already speaks this exact wire format
// (POST {baseUrl}/v1/chat/completions, Bearer auth, `data: {...}` SSE
// chunks ending `data: [DONE]`) against README's documented but
// previously-unbuilt "hermes-gateway (local OpenAI-compatible reverse
// proxy in front of hermes-agent instances)". This is that proxy, for
// real, not a description of one.
//
// One simplification, stated rather than hidden: `hermes chat --oneshot`
// only returns a complete answer when the process exits, so this cannot
// stream token-by-token the way a real model API does. It sends the whole
// answer as a single SSE chunk, which the dashboard's existing parser
// (dashboard/src/api/gateway.ts's diagnoseStream()) already handles fine
// -- it just renders in one step instead of trickling in.
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { writeFile, unlink, mkdtemp } from "node:fs/promises";
import { tmpdir, homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Same .env server/index.mjs already loads (process.loadEnvFile, Node
// 20.6+ built-in) -- one shared secret, one file, so there is nothing to
// keep in sync by hand between the two processes.
try {
  process.loadEnvFile(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", ".env"),
  );
} catch {
  // No .env: falls through to requiredKey() below returning "", which
  // rejects every request with 401 rather than running unauthenticated.
}

const PORT = Number(process.env.HERMES_GATEWAY_PORT || 8700);
const HOST = process.env.HERMES_GATEWAY_HOST || "127.0.0.1";

// Which profile this gateway serves, and what model name callers ask
// for. One gateway, one profile, by design -- README's "in front of
// hermes-agent instances" (plural) describes an aspiration this repo
// never built; this is the one instance ppa-eda-agent actually needs.
const PROFILE = process.env.HERMES_GATEWAY_PROFILE || "ppa-agent";
const MODEL_NAME = process.env.HERMES_GATEWAY_MODEL || "ppa-agent";

// Bearer key this gateway requires from callers. Deliberately its own
// env var, read by this process, not by ppa-eda-agent's server/index.mjs
// -- the two are set to the same value (see .env), but each side reads
// its own copy, matching the existing PPA_EDA_GATEWAY_KEY /
// PPA_EDA_DIRECT_LLM_KEY split of "each process holds only the
// credential it needs".
function requiredKey() {
  // HERMES_GATEWAY_KEY if set separately; otherwise the same key
  // server/index.mjs already uses to call this gateway
  // (PPA_EDA_GATEWAY_KEY) -- one .env, one value, both sides.
  return (process.env.HERMES_GATEWAY_KEY || process.env.PPA_EDA_GATEWAY_KEY || "").trim();
}

// Hermes install layout (see hermes-desktop-main/src/main/installer.ts,
// which this mirrors): %LOCALAPPDATA%\hermes on Windows, ~/.hermes
// elsewhere, overridable because a different machine's install really
// can live somewhere else.
const HERMES_HOME =
  process.env.HERMES_HOME ||
  (process.platform === "win32" && process.env.LOCALAPPDATA
    ? path.join(process.env.LOCALAPPDATA, "hermes")
    : path.join(homedir(), ".hermes"));
const HERMES_REPO = path.join(HERMES_HOME, "hermes-agent");
const HERMES_PYTHON =
  process.platform === "win32"
    ? path.join(HERMES_REPO, "venv", "Scripts", "python.exe")
    : path.join(HERMES_REPO, "venv", "bin", "python");

// The real reply sits between Hermes's own box-drawing banner lines in
// its terminal output, wrapped in ANSI colour codes for a TTY that isn't
// there. Stripped rather than requested away: --oneshot has no
// machine-readable output mode, so this is the actual contract of the
// CLI's stdout, not a workaround for a bug.
const ANSI = /\x1b\[[0-9;]*m/g;
// The top line reads "╭─ ⚕ Hermes ────╮" -- real letters in the title,
// so this matches on the leading box-drawing character only, not the
// whole line.
const BOX_TOP = /^\s*╭/;
const BOX_BOTTOM = /^\s*╰/;

function extractReply(rawStdout) {
  const clean = rawStdout.replace(ANSI, "");
  const lines = clean.split("\n");
  const top = lines.findIndex((l) => BOX_TOP.test(l.trim()));
  if (top === -1) return null;
  const bottom = lines.findIndex(
    (l, i) => i > top && BOX_BOTTOM.test(l.trim()),
  );
  if (bottom === -1) return null;
  return lines.slice(top + 1, bottom).join("\n").trim();
}

// Runs one real hermes-agent turn and returns its reply text.
// --query-file rather than -q: the CLI's own stated reason is that
// nothing in the file is shell-interpreted, which matters here because
// prompt content is a pasted EDA report -- may contain quotes, `$(...)`,
// backticks, anything.
async function runHermes(prompt) {
  const dir = await mkdtemp(path.join(tmpdir(), "ppa-agent-"));
  const queryFile = path.join(dir, "query.txt");
  await writeFile(queryFile, prompt, "utf-8");
  try {
    return await new Promise((resolve, reject) => {
      const proc = spawn(
        HERMES_PYTHON,
        [
          // --profile is a global flag, parsed before the subcommand
          // name -- `hermes chat ... --profile X` is a parse error.
          "-m", "hermes_cli.main",
          "--profile", PROFILE,
          "chat", "--query-file", queryFile, "--oneshot",
        ],
        {
          cwd: HERMES_REPO,
          env: { ...process.env, HERMES_HOME },
        },
      );
      let out = "";
      let err = "";
      proc.stdout.on("data", (d) => (out += d));
      proc.stderr.on("data", (d) => (err += d));
      proc.on("error", reject);
      proc.on("close", (code) => {
        if (code !== 0) {
          reject(new Error(`hermes chat exited ${code}: ${err.slice(-800) || out.slice(-800)}`));
          return;
        }
        const reply = extractReply(out);
        if (reply === null) {
          reject(new Error(`could not parse hermes output: ${out.slice(-800)}`));
          return;
        }
        resolve(reply);
      });
    });
  } finally {
    await unlink(queryFile).catch(() => {});
  }
}

function sseChunk(content) {
  return `data: ${JSON.stringify({ choices: [{ delta: { content } }] })}\n\n`;
}

const server = createServer(async (req, res) => {
  if (req.method !== "POST" || req.url !== "/v1/chat/completions") {
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
    return;
  }

  const key = requiredKey();
  const auth = req.headers.authorization || "";
  const given = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!key || given !== key) {
    res.writeHead(401, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "invalid or missing bearer key" }));
    return;
  }

  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", async () => {
    let parsed;
    try {
      parsed = JSON.parse(body || "{}");
    } catch {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "invalid JSON body" }));
      return;
    }
    const messages = Array.isArray(parsed.messages) ? parsed.messages : [];
    const last = messages[messages.length - 1];
    const prompt = typeof last?.content === "string" ? last.content : "";
    if (!prompt.trim()) {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "messages[].content required" }));
      return;
    }

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Hermes-Gateway-Upstream": MODEL_NAME,
    });

    try {
      const reply = await runHermes(prompt);
      res.write(sseChunk(reply));
      res.write("data: [DONE]\n\n");
    } catch (err) {
      // Headers are already sent (SSE), so the error has to travel as a
      // chunk of "content" rather than a fresh HTTP status -- same
      // constraint proxyChat() documents on the ppa-eda-agent side.
      res.write(sseChunk(`[hermes-gateway error] ${err.message ?? err}`));
      res.write("data: [DONE]\n\n");
      console.error("[hermes-gateway]", err);
    }
    res.end();
  });
});

server.listen(PORT, HOST, () => {
  console.log(
    `hermes-gateway listening on http://${HOST}:${PORT} ` +
    `(model "${MODEL_NAME}" -> hermes profile "${PROFILE}", home ${HERMES_HOME})`,
  );
  if (!requiredKey()) {
    console.log(
      "[hermes-gateway] HERMES_GATEWAY_KEY is not set -- every request will " +
      "be rejected with 401 until it is.",
    );
  }
});

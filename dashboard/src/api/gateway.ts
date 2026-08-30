import type { Lang } from "../i18n";
const STORAGE_KEY = "ppa-eda-agent-dashboard:gateway-key";
export const GATEWAY_BASE_URL = "http://127.0.0.1:8700";
export const MODEL = "ppa-eda-analyst";
const LOCAL_SERVER_URL = "http://127.0.0.1:8123";

export function getStoredKey(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

export function setStoredKey(key: string): void {
  localStorage.setItem(STORAGE_KEY, key);
}

export function clearStoredKey(): void {
  localStorage.removeItem(STORAGE_KEY);
}

// Whether server/index.mjs has its own PPA_EDA_GATEWAY_KEY configured —
// pattern borrowed from ~/gitspace/mi-report/backend/app/agentchat.py:
// a server-side credential the browser never has to handle, checked via
// a real endpoint rather than assumed. When true, the dashboard can skip
// asking the user to paste a key at all.
export async function checkGatewayStatus(): Promise<boolean> {
  try {
    const res = await fetch(`${LOCAL_SERVER_URL}/gateway-status`);
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data?.configured);
  } catch {
    return false;
  }
}

// Asked of the model directly rather than translating its answer
// afterwards: a Korean diagnosis written as Korean is not a translation
// of an English one, and it saves the user a second step.
//
// Identifiers are pinned explicitly because a model told to answer in
// Korean will otherwise localise the things that must not change —
// metric keys, error codes, signal and file names.
export function languageInstruction(lang: Lang): string {
  const keep =
    " Keep every number, unit, signal name, file path, metric key, tool " +
    "name and error code exactly as written — translate the prose around " +
    "them, never the identifiers.";
  // English is stated explicitly, not left blank: measured, this
  // gateway's model answers in Korean when given no instruction at all,
  // so an empty English case would make the toggle do nothing.
  return lang === "ko"
    ? "\n\nRespond in Korean (한국어)." + keep
    : "\n\nRespond in English." + keep;
}

export interface DiagnoseCallbacks {
  onToken: (delta: string) => void;
  onDone: (upstreamHeader: string | null) => void;
  onError: (err: Error) => void;
}

async function pipeSse(
  res: Response,
  { onToken, onDone }: DiagnoseCallbacks
): Promise<void> {
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }

  const upstreamHeader = res.headers.get("X-Hermes-Gateway-Upstream");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const payload = trimmed.slice("data:".length).trim();
      if (payload === "[DONE]") {
        onDone(upstreamHeader);
        return;
      }
      try {
        const parsed = JSON.parse(payload);
        const delta = parsed?.choices?.[0]?.delta?.content;
        if (typeof delta === "string" && delta.length > 0) {
          onToken(delta);
        }
      } catch {
        // Ignore malformed keepalive/partial lines — next chunk will
        // usually complete them.
      }
    }
  }
  onDone(upstreamHeader);
}

// Streams the diagnosis token-by-token (SSE) instead of waiting for the
// full response — makes the live agent call visibly happening, not a
// silent wait followed by a text dump. See hermes-gateway skill's
// troubleshooting.md "SSE looks buffered" section for the wire format
// this parses (OpenAI-style `data: {...}` chunks, ending `data: [DONE]`).
//
// Browser-direct path: the user pastes their own gateway client key
// (stored only in localStorage, sent straight to hermes-gateway). Use
// diagnoseViaServer() instead when checkGatewayStatus() reports the
// server has its own key configured — same wire format either way.
export async function diagnoseStream(
  key: string,
  reportText: string,
  callbacks: DiagnoseCallbacks,
  lang: Lang = "en"
): Promise<void> {
  try {
    const res = await fetch(`${GATEWAY_BASE_URL}/v1/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: MODEL,
        stream: true,
        messages: [
          {
            role: "user",
            content:
              `Diagnose this OpenSTA simulation output:\n\n${reportText}` +
              languageInstruction(lang),
          },
        ],
      }),
    });
    await pipeSse(res, callbacks);
  } catch (e) {
    callbacks.onError(e instanceof Error ? e : new Error(String(e)));
  }
}

// Server-proxied path: server/index.mjs holds PPA_EDA_GATEWAY_KEY in its
// own environment and forwards to hermes-gateway — the browser never
// sees a key at all. Same SSE wire format as diagnoseStream(), so
// callers don't need to branch on which path is active beyond choosing
// which function to call.
export async function diagnoseViaServer(
  reportText: string,
  callbacks: DiagnoseCallbacks,
  lang: Lang = "en"
): Promise<void> {
  try {
    const res = await fetch(`${LOCAL_SERVER_URL}/diagnose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reportText, lang }),
    });
    await pipeSse(res, callbacks);
  } catch (e) {
    callbacks.onError(e instanceof Error ? e : new Error(String(e)));
  }
}

// On-demand machine translation for real reference-db content (pipeline
// diagnosis text, human-in-the-loop review summaries) — the dashboard's
// i18n only covers UI chrome, never this real subagent-written evidence,
// since a precise number (a transition time, a capacitance) silently
// mistranslated would misrepresent an actual finding. Same browser-direct
// / server-proxied split and SSE wire format as diagnose*() above.
export async function translateStream(
  key: string,
  text: string,
  callbacks: DiagnoseCallbacks
): Promise<void> {
  try {
    const res = await fetch(`${GATEWAY_BASE_URL}/v1/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: MODEL,
        stream: true,
        messages: [
          {
            role: "user",
            content:
              "Translate the following semiconductor design (EDA/OpenLane/" +
              "timing) diagnosis text into Korean. Preserve every number, " +
              "unit, signal name, file path, and technical term exactly as " +
              "written — do not round, re-derive, or omit any figure. " +
              "Output only the translation, no commentary:\n\n" + text,
          },
        ],
      }),
    });
    await pipeSse(res, callbacks);
  } catch (e) {
    callbacks.onError(e instanceof Error ? e : new Error(String(e)));
  }
}

// Streams a real second opinion on a stuck run from hermes-gateway,
// given the review request the pipeline generated. Server-proxied only:
// unlike diagnosis, this one has no browser-direct variant, because the
// prompt is assembled server-side alongside the request file it reviews.
/** The draft already written for this design's current request, if any.
 *
 * Asked before offering to generate one, because asking the model costs
 * a real multi-minute call and the answer to "what did it say" is
 * usually already on disk.
 */
export async function cachedReview(
  design: string,
  requestText: string,
  lang: Lang
): Promise<{ text: string | null; written_at: string | null }> {
  // The request text goes with the ask so the server can confirm the
  // stored draft was written for THIS request. Without that check a
  // draft from another case — or the same case asked in another
  // language — comes back looking like an answer.
  const res = await fetch(`${LOCAL_SERVER_URL}/review/cached`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ design, requestText, lang }),
  });
  if (!res.ok) return { text: null, written_at: null };
  return res.json();
}

export async function askReview(
  requestText: string,
  callbacks: DiagnoseCallbacks,
  lang: Lang = "en",
  // Passing the design lets the server cache the draft; `refresh` asks
  // it to write a new one over the cached answer.
  options: { design?: string; refresh?: boolean } = {}
): Promise<void> {
  try {
    const res = await fetch(`${LOCAL_SERVER_URL}/review/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requestText, lang, ...options }),
    });
    await pipeSse(res, callbacks);
  } catch (e) {
    callbacks.onError(e instanceof Error ? e : new Error(String(e)));
  }
}

export interface AskSource {
  source: string;
  title: string;
  score: number;
  matched: string[];
  excerpt: string;
}

export interface AskGrounding {
  sources: AskSource[];
  /** Counts read live from reference-db, when the question is about results. */
  facts: string | null;
}

/** What the repo holds on this question, with no model involved.
 *
 * Called first and on its own, because it is the half that works
 * without a hermes-gateway key. A checkout that has not configured one
 * still gets "here is the section that answers this, in collect.py",
 * which is a worse answer than a written one and a far better answer
 * than an empty box.
 */
export async function askSources(question: string): Promise<AskGrounding> {
  const res = await fetch(`${LOCAL_SERVER_URL}/ask/sources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

/** The written answer, grounded in those same sources. */
export async function askViaServer(
  question: string,
  callbacks: DiagnoseCallbacks
): Promise<void> {
  try {
    const res = await fetch(`${LOCAL_SERVER_URL}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    // Retrieval found nothing, so the server declined to ask the model
    // rather than inviting it to invent an answer. It says so in JSON
    // instead of streaming, and reading that as a stream would show the
    // reader an empty answer with no reason for it.
    if (res.headers.get("Content-Type")?.includes("application/json")) {
      const body = await res.json();
      callbacks.onError(new Error(body.error ?? "ungrounded"));
      return;
    }
    await pipeSse(res, callbacks);
  } catch (e) {
    callbacks.onError(e instanceof Error ? e : new Error(String(e)));
  }
}

export async function translateViaServer(
  text: string,
  callbacks: DiagnoseCallbacks
): Promise<void> {
  try {
    const res = await fetch(`${LOCAL_SERVER_URL}/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    await pipeSse(res, callbacks);
  } catch (e) {
    callbacks.onError(e instanceof Error ? e : new Error(String(e)));
  }
}

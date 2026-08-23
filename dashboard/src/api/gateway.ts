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
            content: `Diagnose this OpenSTA simulation output:\n\n${reportText}`,
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
  callbacks: DiagnoseCallbacks
): Promise<void> {
  try {
    const res = await fetch(`${LOCAL_SERVER_URL}/diagnose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reportText }),
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
export async function askReview(
  requestText: string,
  callbacks: DiagnoseCallbacks
): Promise<void> {
  try {
    const res = await fetch(`${LOCAL_SERVER_URL}/review/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requestText }),
    });
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

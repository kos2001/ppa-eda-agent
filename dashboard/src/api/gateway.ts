const STORAGE_KEY = "ppa-eda-agent-dashboard:gateway-key";
export const GATEWAY_BASE_URL = "http://127.0.0.1:8700";
export const MODEL = "ppa-eda-analyst";

export function getStoredKey(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

export function setStoredKey(key: string): void {
  localStorage.setItem(STORAGE_KEY, key);
}

export function clearStoredKey(): void {
  localStorage.removeItem(STORAGE_KEY);
}

export interface DiagnoseCallbacks {
  onToken: (delta: string) => void;
  onDone: (upstreamHeader: string | null) => void;
  onError: (err: Error) => void;
}

// Streams the diagnosis token-by-token (SSE) instead of waiting for the
// full response — makes the live agent call visibly happening, not a
// silent wait followed by a text dump. See hermes-gateway skill's
// troubleshooting.md "SSE looks buffered" section for the wire format
// this parses (OpenAI-style `data: {...}` chunks, ending `data: [DONE]`).
export async function diagnoseStream(
  key: string,
  reportText: string,
  { onToken, onDone, onError }: DiagnoseCallbacks
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
  } catch (e) {
    onError(e instanceof Error ? e : new Error(String(e)));
  }
}

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

export async function diagnose(key: string, reportText: string): Promise<string> {
  const res = await fetch(`${GATEWAY_BASE_URL}/v1/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: MODEL,
      messages: [
        {
          role: "user",
          content: `Diagnose this OpenSTA simulation output:\n\n${reportText}`,
        },
      ],
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }

  const data = await res.json();
  const content = data?.choices?.[0]?.message?.content;
  if (typeof content !== "string") {
    throw new Error("Unexpected response shape from gateway");
  }
  return content;
}

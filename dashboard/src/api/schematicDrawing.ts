export type DrawingPhase = "preparing" | "queued" | "rendering";

export function waitForDrawing(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function fetchDrawing(
  source: URL, signal: AbortSignal, onPhase: (phase: DrawingPhase) => void,
  fetcher: typeof fetch = fetch, wait = waitForDrawing,
): Promise<string> {
  let url = source;
  const deadline = Date.now() + 30 * 60_000;
  while (Date.now() < deadline) {
    signal.throwIfAborted();
    const response = await fetcher(url, { signal, cache: "no-store" });
    if (response.status === 202) {
      const job = await response.json();
      if (!["preparing", "queued", "rendering"].includes(job.state) || typeof job.poll !== "string") {
        throw new Error("Invalid drawing task response");
      }
      // Poll paths are relative to the original /analog/svg endpoint.
      const next = new URL(job.poll, source);
      if (next.origin !== source.origin) throw new Error("Invalid drawing task URL");
      onPhase(job.state);
      url = next;
      await wait(1000, signal);
      continue;
    }
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(typeof body?.error === "string" ? body.error : `Drawing unavailable (${response.status})`);
    }
    if (!response.headers.get("content-type")?.includes("image/svg+xml")) {
      throw new Error("Renderer returned an unexpected drawing format");
    }
    return response.text();
  }
  throw new Error("Drawing preparation timed out; retry");
}

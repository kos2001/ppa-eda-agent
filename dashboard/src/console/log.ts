// The console pane's backing store — a real event log, not a decoration.
//
// Every commercial EDA tool has one: Virtuoso's CIW, Innovus's console,
// Calibre's transcript. It is the first place an engineer looks when
// something did not happen, and it earns that by carrying what the tool
// actually did, in order, with timings. A pane that showed invented
// "system ready" lines would be the fabricated-metric failure soul.md
// rules out, moved into the chrome.
//
// So the only writer that runs by default is `installFetchLogging()`,
// which wraps window.fetch and records real backend calls: method, path,
// status and measured duration. Everything else in here is a plain
// `log()` call made by code at the moment it really does something.

export type LogLevel = "info" | "warn" | "error" | "cmd";

export type LogEntry = {
  id: number;
  at: number;
  level: LogLevel;
  source: string;
  text: string;
};

// Bounded because this runs for as long as the tab is open and a
// pipeline run emits a line every poll. 500 is roughly an hour of
// polling at the pipeline tab's 5 s interval — enough to scroll back
// through a run, short of holding a session's worth of DOM.
const MAX_ENTRIES = 500;

const entries: LogEntry[] = [];
const listeners = new Set<() => void>();
let nextId = 1;

function emit() {
  for (const fn of listeners) fn();
}

export function log(level: LogLevel, source: string, text: string): void {
  entries.push({ id: nextId++, at: Date.now(), level, source, text });
  if (entries.length > MAX_ENTRIES) entries.splice(0, entries.length - MAX_ENTRIES);
  emit();
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getEntries(): LogEntry[] {
  return entries;
}

export function clear(): void {
  entries.length = 0;
  log("info", "console", "transcript cleared");
}

export function formatTime(at: number): string {
  const d = new Date(at);
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

/** The transcript as plain text, for "save transcript" — the same thing
 *  every one of these tools lets you do with its log. */
export function toText(): string {
  return entries
    .map((e) => `${formatTime(e.at)}  ${e.level.toUpperCase().padEnd(5)} ${e.source}: ${e.text}`)
    .join("\n");
}

// Which calls are worth a line. Vite's own HMR and module requests go
// through fetch too, and a transcript that logged them would bury the
// backend traffic it exists to show.
function isBackendCall(url: string): boolean {
  return url.includes("127.0.0.1:8123") || url.includes("localhost:8123");
}

function shortPath(url: string): string {
  try {
    const u = new URL(url, window.location.href);
    return u.pathname + u.search;
  } catch {
    return url;
  }
}

let installed = false;

/** Wrap window.fetch once so the transcript carries real backend traffic.
 *
 *  Deliberately a wrapper rather than a call-site change in each of the
 *  api/ modules: those are where the app's real requests live, and the
 *  point of a transcript is that nothing gets to make a request without
 *  appearing in it. */
export function installFetchLogging(): void {
  if (installed) return;
  installed = true;
  const original = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!isBackendCall(url)) return original(input as RequestInfo, init);
    const method = (init?.method ?? (typeof input === "object" && "method" in input ? input.method : "GET") ?? "GET").toUpperCase();
    const started = performance.now();
    try {
      const res = await original(input as RequestInfo, init);
      const ms = Math.round(performance.now() - started);
      log(res.ok ? "info" : "error", "backend",
          `${method} ${shortPath(url)} → ${res.status} ${res.statusText || ""} (${ms} ms)`.trim());
      return res;
    } catch (err) {
      const ms = Math.round(performance.now() - started);
      // A backend that is not running is the single most common reason
      // a panel sits empty, and fetch reports it as a bare TypeError
      // with no URL in the message. Naming the endpoint here is what
      // turns "Failed to fetch" into something actionable.
      log("error", "backend",
          `${method} ${shortPath(url)} → ${String((err as Error).message ?? err)} (${ms} ms)`);
      throw err;
    }
  };
}

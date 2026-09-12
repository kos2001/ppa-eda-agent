// The chrome a commercial EDA console has and this one did not: a menu
// bar, a docked transcript, and a status bar carrying real tool state.
//
// Innovus, Virtuoso, Calibre and PrimeTime look alike for a reason.
// Their users spend the day in one window, so the window says at all
// times what tools are up, what it just did, and where every command
// lives — and it spends no pixels on anything else. This app had a
// marketing-shaped header (a subtitle and a decorative animated signal
// line) and no transcript at all.
//
// The rule applied throughout: a control that cannot do something real
// is not added. Every menu item below either navigates, toggles a
// setting that persists, or writes a file; the status bar reads
// /gateway-status and /toolchain-status rather than asserting
// "connected"; the transcript carries measured backend calls. A fake
// File > Save would look more like Virtuoso and mean less than nothing.
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  clear as clearLog,
  formatTime,
  getEntries,
  log,
  subscribe,
  toText,
  type LogEntry,
} from "../console/log";
import "./EdaShell.css";

export const BACKEND = "http://127.0.0.1:8123";

// ---------------------------------------------------------------------
// Menu bar
// ---------------------------------------------------------------------

export type MenuItem = {
  label: string;
  onSelect?: () => void;
  checked?: boolean;
  disabled?: boolean;
  separatorBefore?: boolean;
  hint?: string;
};

export type Menu = { label: string; items: MenuItem[] };

export function MenuBar({ menus, right }: { menus: Menu[]; right?: React.ReactNode }) {
  const [open, setOpen] = useState<string | null>(null);
  const barRef = useRef<HTMLDivElement>(null);

  // Desktop-menu behaviour, not a set of independent dropdowns: once one
  // is open, moving across the bar switches to the next without a second
  // click, and Escape or a click anywhere else closes. Getting this
  // wrong is the tell that a menu bar is a row of buttons in costume.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!barRef.current?.contains(e.target as Node)) setOpen(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(null);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="eda-menubar" ref={barRef}>
      {menus.map((menu) => (
        <div className="eda-menubar__slot" key={menu.label}>
          <button
            type="button"
            className={open === menu.label ? "eda-menubar__button is-open" : "eda-menubar__button"}
            aria-haspopup="menu"
            aria-expanded={open === menu.label}
            onClick={() => setOpen(open === menu.label ? null : menu.label)}
            onMouseEnter={() => open && setOpen(menu.label)}
          >
            {menu.label}
          </button>
          {open === menu.label && (
            <div className="eda-menu" role="menu">
              {menu.items.map((item, i) => (
                <button
                  type="button"
                  key={`${item.label}-${i}`}
                  role="menuitemcheckbox"
                  aria-checked={item.checked ?? false}
                  className={
                    "eda-menu__item" +
                    (item.separatorBefore ? " eda-menu__item--sep" : "") +
                    (item.checked ? " is-checked" : "")
                  }
                  disabled={item.disabled}
                  onClick={() => {
                    setOpen(null);
                    item.onSelect?.();
                  }}
                >
                  <span className="eda-menu__check">{item.checked ? "✓" : ""}</span>
                  <span className="eda-menu__label">{item.label}</span>
                  {item.hint && <span className="eda-menu__hint">{item.hint}</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
      <div className="eda-menubar__right">{right}</div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Transcript (CIW / Innovus console analogue)
// ---------------------------------------------------------------------

export function ConsolePane({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const entries = useSyncExternalStore(subscribe, getEntries);
  const [minLevel, setMinLevel] = useState<"all" | "warn" | "error">("all");
  const bodyRef = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);

  const shown = entries.filter((e) => {
    if (minLevel === "all") return true;
    if (minLevel === "warn") return e.level === "warn" || e.level === "error" || e.level === "cmd";
    return e.level === "error";
  });

  useEffect(() => {
    if (!open || !follow) return;
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length, open, follow]);

  const counts = entries.reduce<Record<string, number>>((acc, e) => {
    acc[e.level] = (acc[e.level] ?? 0) + 1;
    return acc;
  }, {});

  const save = useCallback(() => {
    const blob = new Blob([toText()], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `dtco-console-${new Date().toISOString().replace(/[:.]/g, "-")}.log`;
    a.click();
    URL.revokeObjectURL(url);
    log("info", "console", `transcript saved (${entries.length} lines)`);
  }, [entries.length]);

  return (
    <section className={open ? "eda-console" : "eda-console eda-console--collapsed"}>
      <header className="eda-console__bar">
        <button type="button" className="eda-console__toggle" onClick={onToggle}
                aria-expanded={open}>
          {open ? "▾" : "▸"} Console
        </button>
        <span className="eda-console__counts">
          <b>{entries.length}</b> lines
          {counts.error ? <em className="is-error"> {counts.error} error</em> : null}
          {counts.warn ? <em className="is-warn"> {counts.warn} warn</em> : null}
        </span>
        {open && (
          <div className="eda-console__tools">
            <label className="eda-console__filter">
              level
              <select value={minLevel} onChange={(e) => setMinLevel(e.target.value as typeof minLevel)}>
                <option value="all">all</option>
                <option value="warn">warn+</option>
                <option value="error">error</option>
              </select>
            </label>
            <label className="eda-console__follow">
              <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
              follow
            </label>
            <button type="button" onClick={save}>save</button>
            <button type="button" onClick={clearLog}>clear</button>
          </div>
        )}
      </header>
      {open && (
        <div className="eda-console__body" ref={bodyRef}
             onScroll={(e) => {
               const el = e.currentTarget;
               // Scrolling up turns follow off the way every terminal
               // does; re-enabling is the checkbox, not a guess.
               if (follow && el.scrollHeight - el.scrollTop - el.clientHeight > 40) setFollow(false);
             }}>
          {shown.length === 0 ? (
            <p className="eda-console__empty">
              No entries yet. Backend calls are recorded here as they happen.
            </p>
          ) : (
            shown.map((e: LogEntry) => (
              <div className={`eda-console__line is-${e.level}`} key={e.id}>
                <span className="eda-console__time">{formatTime(e.at)}</span>
                <span className="eda-console__level">{e.level}</span>
                <span className="eda-console__source">{e.source}</span>
                <span className="eda-console__text">{e.text}</span>
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------

type BackendEntry = { available: boolean; via: string | null; version: string | null;
                      virtuoso_equivalent: string };

type Toolchain = {
  metadata?: { backends?: Record<string, BackendEntry>; available?: string[];
               missing?: string[]; pdks?: string[] };
};

export function StatusBar({ design, tab }: { design: string | null; tab: string }) {
  const [gateway, setGateway] = useState<"?" | "ok" | "error">("?");
  const [configured, setConfigured] = useState(false);
  const [tools, setTools] = useState<Toolchain | null>(null);
  const [clock, setClock] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${BACKEND}/gateway-status`);
        const body = await res.json();
        if (cancelled) return;
        setGateway("ok");
        setConfigured(Boolean(body.configured));
      } catch {
        if (!cancelled) setGateway("error");
      }
    };
    poll();
    const id = setInterval(poll, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    // Asked once per mount rather than on a timer: what it reports
    // changes when the machine changes, and the server caches it for a
    // minute anyway.
    fetch(`${BACKEND}/toolchain-status`)
      .then((r) => r.json())
      .then((body) => { if (!cancelled) setTools(body); })
      .catch(() => { /* the gateway pill already says the backend is down */ });
    return () => { cancelled = true; };
  }, []);

  const backends = tools?.metadata?.backends ?? {};
  const pdks = tools?.metadata?.pdks ?? [];

  return (
    <footer className="eda-status">
      <span className={`eda-status__pill is-${gateway}`} title={`${BACKEND}`}>
        <i />
        {gateway === "ok" ? "server 8123" : gateway === "error" ? "server offline" : "server …"}
      </span>
      <span className="eda-status__pill" title="hermes-gateway key resolved server-side">
        <i className={configured ? "is-good" : "is-idle"} />
        gateway {configured ? "keyed" : "unkeyed"}
      </span>
      <span className="eda-status__sep" />
      {Object.entries(backends).map(([name, entry]) => (
        <span
          key={name}
          className={entry.available ? "eda-status__tool is-ok" : "eda-status__tool"}
          title={`${entry.virtuoso_equivalent}${entry.version ? ` — ${entry.version}` : ""}${
            entry.via ? ` (${entry.via})` : " (not installed)"}`}
        >
          {name}
          <b>{entry.available ? (entry.via === "docker" ? "▣" : "●") : "—"}</b>
        </span>
      ))}
      <span className="eda-status__spacer" />
      {pdks.length > 0 && <span className="eda-status__field">PDK {pdks.join(" · ")}</span>}
      {design && <span className="eda-status__field">design {design}</span>}
      <span className="eda-status__field">view {tab}</span>
      <span className="eda-status__clock">{clock.toLocaleTimeString()}</span>
    </footer>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { useLang } from "../i18n";
import { useAgent } from "../agentContext";
import "./AgentSidebar.css";

const CHECKLIST_KEYS = [
  "agent_checklist_1",
  "agent_checklist_2",
  "agent_checklist_3",
  "agent_checklist_4",
  "agent_checklist_5",
] as const;

const WIDTH_STORAGE_KEY = "ppa-eda-agent-dashboard:sidebar-width";
const DEFAULT_WIDTH = 420;
const MIN_WIDTH = 300;
const MAX_WIDTH = 780;

export default function AgentSidebar() {
  const { t } = useLang();
  const {
    key,
    saveKey,
    diagnosing,
    streamedText,
    tokenCount,
    elapsedMs,
    confirmedUpstream,
    error,
  } = useAgent();
  const [keyInput, setKeyInput] = useState("");
  const hasActivity = diagnosing || streamedText.length > 0;

  const [width, setWidth] = useState<number>(() => {
    const stored = Number(localStorage.getItem(WIDTH_STORAGE_KEY));
    return stored >= MIN_WIDTH && stored <= MAX_WIDTH ? stored : DEFAULT_WIDTH;
  });
  const resizing = useRef(false);

  const onPointerMove = useCallback((e: PointerEvent) => {
    if (!resizing.current) return;
    // Sidebar is on the right edge, so dragging left (smaller clientX)
    // should grow it — width is measured from the viewport's right edge.
    const newWidth = window.innerWidth - e.clientX;
    const clamped = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, newWidth));
    setWidth(clamped);
  }, []);

  const stopResizing = useCallback(() => {
    if (!resizing.current) return;
    resizing.current = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    setWidth((w) => {
      localStorage.setItem(WIDTH_STORAGE_KEY, String(w));
      return w;
    });
  }, []);

  useEffect(() => {
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", stopResizing);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", stopResizing);
    };
  }, [onPointerMove, stopResizing]);

  function startResizing(e: React.PointerEvent) {
    e.preventDefault();
    resizing.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }

  return (
    <aside className="agent-sidebar" style={{ width }}>
      <div
        className="agent-sidebar__resize-handle"
        onPointerDown={startResizing}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize agent sidebar"
      />
      <div className="agent-sidebar__header">
        <span
          className={
            diagnosing
              ? "agent-sidebar__status agent-sidebar__status--live"
              : "agent-sidebar__status"
          }
        >
          {diagnosing ? t("agent_streaming_live") : "○ " + t("agent_sidebar_title")}
        </span>
        <p className="agent-sidebar__subtitle">{t("agent_sidebar_subtitle")}</p>
      </div>

      <div className="agent-sidebar__checklist">
        <span className="tab__meta-label">{t("agent_checklist_title")}</span>
        <ol>
          {CHECKLIST_KEYS.map((k) => (
            <li key={k}>{t(k)}</li>
          ))}
        </ol>
      </div>

      <div className="agent-sidebar__body">
        {!hasActivity && <p className="agent-sidebar__idle">{t("agent_idle")}</p>}

        {hasActivity && (
          <>
            <div className="agent-sidebar__meta">
              <span>{(elapsedMs / 1000).toFixed(1)}s</span>
              <span>
                {tokenCount} {t("agent_tokens")}
              </span>
            </div>
            <div className="agent-sidebar__stream">
              {streamedText}
              {diagnosing && <span className="agent-sidebar__cursor">▍</span>}
            </div>
            {confirmedUpstream && !diagnosing && (
              <p className="agent-sidebar__confirmed">
                {t("agent_confirmed_upstream")}{" "}
                <code>{confirmedUpstream}</code>
              </p>
            )}
          </>
        )}

        {error && <div className="tab__error">{error}</div>}
      </div>

      {!key && (
        <div className="agent-sidebar__keysetup">
          <input
            type="password"
            placeholder="gw-..."
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && keyInput.trim()) saveKey(keyInput.trim());
            }}
          />
          <button
            onClick={() => keyInput.trim() && saveKey(keyInput.trim())}
          >
            {t("save_key")}
          </button>
        </div>
      )}
    </aside>
  );
}

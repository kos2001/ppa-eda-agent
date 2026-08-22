import { useEffect, useState } from "react";
import { useLang } from "../i18n";
import { useAgent } from "../agentContext";
import "./Tabs.css";
import "./DiagnosisPage.css";

const CHECKLIST_KEYS = [
  "agent_checklist_1",
  "agent_checklist_2",
  "agent_checklist_3",
  "agent_checklist_4",
  "agent_checklist_5",
] as const;

export default function DiagnosisPage() {
  const { t } = useLang();
  const {
    key,
    saveKey,
    serverConfigured,
    diagnosing,
    streamedText,
    tokenCount,
    elapsedMs,
    confirmedUpstream,
    error,
    markResultSeen,
  } = useAgent();
  const [keyInput, setKeyInput] = useState("");

  // Viewing this page is what "sees" the result — clears the unseen badge.
  useEffect(() => {
    markResultSeen();
  }, [markResultSeen]);

  const hasActivity = diagnosing || streamedText.length > 0;
  // Server-side PPA_EDA_GATEWAY_KEY (see server/index.mjs, pattern from
  // ~/gitspace/mi-report/backend/app/agentchat.py) means no pasted key
  // is needed at all — the browser never handles a credential.
  const isReady = Boolean(key) || serverConfigured;

  return (
    <div className="tab diagnosis-page">
      <div className="panel">
        <span className="panel__title">
          <span
            className={
              diagnosing
                ? "agent-sidebar__status agent-sidebar__status--live"
                : "agent-sidebar__status"
            }
          >
            {diagnosing ? t("agent_streaming_live") : "○ " + t("agent_sidebar_title")}
          </span>
        </span>
        <div className="panel__body">
          <p className="diagnosis-page__subtitle">{t("agent_sidebar_subtitle")}</p>
        </div>
      </div>

      <div className="panel">
        <span className="panel__title">{t("agent_checklist_title")}</span>
        <div className="panel__body">
          <ol className="diagnosis-page__checklist">
            {CHECKLIST_KEYS.map((k) => (
              <li key={k}>{t(k)}</li>
            ))}
          </ol>
        </div>
      </div>

      {serverConfigured && !key && (
        <div className="panel">
          <div className="panel__body">
            <p className="agent-sidebar__confirmed">{t("server_key_configured")}</p>
          </div>
        </div>
      )}

      {!isReady && (
        <div className="panel">
          <span className="panel__title">{t("sim_diagnosis_title")}</span>
          <div className="panel__body chat-setup">
            <p>{t("key_input_prompt")}</p>
            <p className="chat-setup__hint">{t("key_input_hint")}</p>
            <input
              type="password"
              placeholder="gw-..."
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && keyInput.trim()) saveKey(keyInput.trim());
              }}
            />
            <div className="report-input__actions">
              <button onClick={() => keyInput.trim() && saveKey(keyInput.trim())}>
                {t("save_key")}
              </button>
            </div>
          </div>
        </div>
      )}

      {!hasActivity && isReady && (
        <div className="panel">
          <div className="panel__body">
            <p className="agent-sidebar__idle">{t("agent_idle")}</p>
          </div>
        </div>
      )}

      {hasActivity && (
        <div className="panel">
          <span className="panel__title">{t("sim_diagnosis_title")}</span>
          <div className="panel__body">
            <div className="agent-sidebar__meta">
              <span>{(elapsedMs / 1000).toFixed(1)}s</span>
              <span>
                {tokenCount} {t("agent_tokens")}
              </span>
            </div>
            <div className="diagnosis-page__stream">
              {streamedText}
              {diagnosing && <span className="agent-sidebar__cursor">▍</span>}
            </div>
            {confirmedUpstream && !diagnosing && (
              <p className="agent-sidebar__confirmed">
                {t("agent_confirmed_upstream")} <code>{confirmedUpstream}</code>
              </p>
            )}
          </div>
        </div>
      )}

      {error && <div className="tab__error">{error}</div>}
    </div>
  );
}

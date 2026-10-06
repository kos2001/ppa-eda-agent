import { useEffect, useRef, useState } from "react";
import { translateStream, translateViaServer } from "../api/gateway";
import { useAgent } from "../agentContext";
import { useLang } from "../i18n";

// On-demand machine translation for real reference-db text (diagnosis,
// review summaries) — only offered when the UI is in Korean, since the
// dashboard's i18n never touches this real subagent-written content
// (precise numbers like transition times/capacitances must stay exactly
// as originally written). Reuses the same hermes-gateway key/server
// config the Diagnosis tab already manages via useAgent(), so there's
// no separate key-entry flow for this feature.
export default function TranslateBlock({ text }: { text: string }) {
  const { lang, t } = useLang();
  const { key, serverConfigured } = useAgent();
  const [translated, setTranslated] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
  }, []);

  if (lang !== "ko") return null;
  const canTranslate = Boolean(key) || serverConfigured;

  function handleTranslate() {
    setLoading(true);
    setTranslated("");
    setError(null);
    setElapsedSec(0);
    const startedAt = performance.now();
    timerRef.current = window.setInterval(() => {
      setElapsedSec(Math.round((performance.now() - startedAt) / 1000));
    }, 1000);
    function stopTimer() {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    const callbacks = {
      // This gateway model doesn't stream token-by-token — it generates
      // the full response server-side and flushes it as one burst (every
      // chunk shares the same upstream timestamp), so onToken may not
      // fire at all until the whole translation is ready. Real long
      // diagnosis text took ~9,800 completion tokens and ~3-4 minutes
      // end to end with zero partial output in between — the elapsed
      // timer below exists so that wait doesn't read as broken.
      onToken: (delta: string) => setTranslated((prev) => (prev ?? "") + delta),
      onDone: () => {
        stopTimer();
        setLoading(false);
      },
      onError: (e: Error) => {
        stopTimer();
        setLoading(false);
        setError(e.message);
      },
    };
    if (serverConfigured) translateViaServer(text, callbacks);
    else if (key) translateStream(key, text, callbacks);
  }

  if (translated !== null) {
    return (
      <div className="pipeline__translation">
        <span className="tab__meta-label">{t("pipeline_translate_label")}</span>
        {loading && translated === "" && (
          <p className="pipeline__translate-waiting">
            {t("pipeline_translate_loading")} ({elapsedSec}s) — {t("pipeline_translate_long_wait_hint")}
          </p>
        )}
        <p>{translated}{loading && translated !== "" && "…"}</p>
        {error && <p className="tab__error">{error}</p>}
      </div>
    );
  }

  return (
    <button
      className="pipeline__translate-button"
      onClick={handleTranslate}
      disabled={!canTranslate || loading}
      title={!canTranslate ? t("pipeline_translate_needs_key") : undefined}
    >
      {loading ? t("pipeline_translate_loading") : t("pipeline_translate_button")}
    </button>
  );
}

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  getStoredKey,
  setStoredKey,
  clearStoredKey,
  diagnoseStream,
  diagnoseViaServer,
  checkGatewayStatus,
} from "./api/gateway";
import { useLang } from "./i18n";

interface AgentState {
  key: string | null;
  saveKey: (k: string) => void;
  clearKey: () => void;
  // True once GET /gateway-status confirms server/index.mjs has its own
  // PPA_EDA_GATEWAY_KEY configured — when true, runDiagnosis() uses the
  // server-proxied path and the UI can skip asking for a pasted key.
  serverConfigured: boolean;
  diagnosing: boolean;
  confirmedUpstream: string | null;
  error: string | null;
  runDiagnosis: (reportText: string) => void;
  hasUnseenResult: boolean;
  markResultSeen: () => void;
}

// The parts of a diagnosis that change many times a second, kept apart
// from AgentState. In one context, the 100 ms elapsed timer and every
// streamed token re-rendered each useAgent() consumer — the whole app
// shell, and whatever tab was open, Pipeline with its layouts included —
// for the minutes a diagnosis runs. Only the Diagnosis page shows these.
interface AgentStream {
  streamedText: string;
  tokenCount: number;
  elapsedMs: number;
}

const AgentContext = createContext<AgentState | null>(null);
const AgentStreamContext = createContext<AgentStream | null>(null);

function notifyBrowser() {
  if (typeof Notification === "undefined") return;
  if (Notification.permission === "granted") {
    new Notification("ppa-eda-analyst", {
      body: "Diagnosis is ready.",
      icon: "/favicon.svg",
    });
  }
}

export function AgentProvider({ children }: { children: ReactNode }) {
  const { lang } = useLang();
  const [key, setKey] = useState<string | null>(getStoredKey());
  const [serverConfigured, setServerConfigured] = useState(false);
  const [diagnosing, setDiagnosing] = useState(false);

  useEffect(() => {
    checkGatewayStatus().then(setServerConfigured);
  }, []);
  const [streamedText, setStreamedText] = useState("");
  const [tokenCount, setTokenCount] = useState(0);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [confirmedUpstream, setConfirmedUpstream] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasUnseenResult, setHasUnseenResult] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (timerRef.current !== null) clearInterval(timerRef.current);
  }, []);

  const saveKey = useCallback((k: string) => {
    setStoredKey(k);
    setKey(k);
  }, []);

  const clearKey = useCallback(() => {
    clearStoredKey();
    setKey(null);
  }, []);

  const markResultSeen = useCallback(() => {
    setHasUnseenResult(false);
  }, []);

  const runDiagnosis = useCallback((reportText: string) => {
    if (diagnosing || (!serverConfigured && !key)) return;

    // Ask for notification permission at the moment the user triggers a
    // run (a real user gesture) — not on page load, which browsers ignore
    // or treat as spammy.
    if (
      typeof Notification !== "undefined" &&
      Notification.permission === "default"
    ) {
      Notification.requestPermission();
    }

    setDiagnosing(true);
    setStreamedText("");
    setTokenCount(0);
    setElapsedMs(0);
    setConfirmedUpstream(null);
    setError(null);
    setHasUnseenResult(false);

    const startedAt = performance.now();
    timerRef.current = window.setInterval(() => {
      setElapsedMs(performance.now() - startedAt);
    }, 100);

    function stopTimer() {
      if (timerRef.current !== null) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }

    const callbacks = {
      onToken: (delta: string) => {
        setStreamedText((prev) => prev + delta);
        setTokenCount((prev) => prev + 1);
      },
      onDone: (upstreamHeader: string | null) => {
        stopTimer();
        setDiagnosing(false);
        setConfirmedUpstream(upstreamHeader);
        setHasUnseenResult(true);
        notifyBrowser();
      },
      onError: (err: Error) => {
        stopTimer();
        setDiagnosing(false);
        setError(err.message);
      },
    };

    // AgentProvider is nested inside LangProvider (App.tsx), so the
    // current language is available here rather than having to be
    // threaded through every caller.
    if (serverConfigured) {
      diagnoseViaServer(reportText, callbacks, lang);
    } else if (key) {
      diagnoseStream(key, reportText, callbacks, lang);
    }
  }, [serverConfigured, key, lang, diagnosing]);

  const value = useMemo<AgentState>(() => ({
    key,
    saveKey,
    clearKey,
    serverConfigured,
    diagnosing,
    confirmedUpstream,
    error,
    runDiagnosis,
    hasUnseenResult,
    markResultSeen,
  }), [key, saveKey, clearKey, serverConfigured, diagnosing, confirmedUpstream,
       error, runDiagnosis, hasUnseenResult, markResultSeen]);
  const stream = useMemo<AgentStream>(
    () => ({ streamedText, tokenCount, elapsedMs }),
    [streamedText, tokenCount, elapsedMs],
  );

  return (
    <AgentContext.Provider value={value}>
      <AgentStreamContext.Provider value={stream}>
        {children}
      </AgentStreamContext.Provider>
    </AgentContext.Provider>
  );
}

export function useAgent(): AgentState {
  const ctx = useContext(AgentContext);
  if (!ctx) throw new Error("useAgent must be used within AgentProvider");
  return ctx;
}

export function useAgentStream(): AgentStream {
  const ctx = useContext(AgentStreamContext);
  if (!ctx) throw new Error("useAgentStream must be used within AgentProvider");
  return ctx;
}

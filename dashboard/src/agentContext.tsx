import {
  createContext,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  getStoredKey,
  setStoredKey,
  clearStoredKey,
  diagnoseStream,
} from "./api/gateway";

interface AgentState {
  key: string | null;
  saveKey: (k: string) => void;
  clearKey: () => void;
  diagnosing: boolean;
  streamedText: string;
  tokenCount: number;
  elapsedMs: number;
  confirmedUpstream: string | null;
  error: string | null;
  runDiagnosis: (reportText: string) => void;
}

const AgentContext = createContext<AgentState | null>(null);

export function AgentProvider({ children }: { children: ReactNode }) {
  const [key, setKey] = useState<string | null>(getStoredKey());
  const [diagnosing, setDiagnosing] = useState(false);
  const [streamedText, setStreamedText] = useState("");
  const [tokenCount, setTokenCount] = useState(0);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [confirmedUpstream, setConfirmedUpstream] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  function saveKey(k: string) {
    setStoredKey(k);
    setKey(k);
  }

  function clearKey() {
    clearStoredKey();
    setKey(null);
  }

  function runDiagnosis(reportText: string) {
    if (!key) return;
    setDiagnosing(true);
    setStreamedText("");
    setTokenCount(0);
    setElapsedMs(0);
    setConfirmedUpstream(null);
    setError(null);

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

    diagnoseStream(key, reportText, {
      onToken: (delta) => {
        setStreamedText((prev) => prev + delta);
        setTokenCount((prev) => prev + 1);
      },
      onDone: (upstreamHeader) => {
        stopTimer();
        setDiagnosing(false);
        setConfirmedUpstream(upstreamHeader);
      },
      onError: (err) => {
        stopTimer();
        setDiagnosing(false);
        setError(err.message);
      },
    });
  }

  return (
    <AgentContext.Provider
      value={{
        key,
        saveKey,
        clearKey,
        diagnosing,
        streamedText,
        tokenCount,
        elapsedMs,
        confirmedUpstream,
        error,
        runDiagnosis,
      }}
    >
      {children}
    </AgentContext.Provider>
  );
}

export function useAgent(): AgentState {
  const ctx = useContext(AgentContext);
  if (!ctx) throw new Error("useAgent must be used within AgentProvider");
  return ctx;
}

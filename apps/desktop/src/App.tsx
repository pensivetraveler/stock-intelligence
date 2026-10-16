import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { version as APP_VERSION } from "../package.json";
import { checkApiHealth, type ApiConnectionState } from "./api-health";
import { createMarketApi } from "./market-api";
import "./styles.css";
import { Workspace } from "./Workspace";

interface AppProps {
  apiBaseUrl?: string;
  fetcher?: typeof fetch;
  retryMs?: number;
  timeoutMs?: number;
}

export function App({
  apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
  fetcher = fetch,
  retryMs = 5_000,
  timeoutMs = 5_000,
}: AppProps) {
  const [state, setState] = useState<ApiConnectionState>("checking");
  const [message, setMessage] = useState("Checking API health");
  const [lastSuccess, setLastSuccess] = useState<string | null>(null);
  const api = useMemo(
    () => createMarketApi(apiBaseUrl, fetcher),
    [apiBaseUrl, fetcher],
  );
  const retryTimer = useRef<number | null>(null);
  const mounted = useRef(true);
  // Bumped whenever the polling loop is replaced (props change or unmount), so a
  // check started by an earlier loop cannot update state or schedule more polls.
  const generation = useRef(0);

  const runCheck = useCallback(async () => {
    const startedGeneration = generation.current;
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    setState("checking");
    setMessage("Checking API health");
    const result = await checkApiHealth(apiBaseUrl, fetcher, timeoutMs);
    if (!mounted.current || startedGeneration !== generation.current) {
      return;
    }
    setState(result.state);
    setMessage(result.message);
    if (result.state === "connected") {
      setLastSuccess(new Date().toISOString());
    }
    retryTimer.current = window.setTimeout(() => void runCheck(), retryMs);
  }, [apiBaseUrl, fetcher, retryMs, timeoutMs]);

  useEffect(() => {
    mounted.current = true;
    generation.current += 1;
    void runCheck();
    return () => {
      mounted.current = false;
      generation.current += 1;
      if (retryTimer.current !== null) {
        window.clearTimeout(retryTimer.current);
        retryTimer.current = null;
      }
    };
  }, [runCheck]);

  return (
    <main className="app">
      <header className="app-bar" aria-labelledby="app-title">
        <div>
          <p className="eyebrow">PHASE 2A CHART WORKSPACE</p>
          <h1 id="app-title">Stock Intelligence Workstation</h1>
          <p className="subtitle">
            Local-first and read-only. Synthetic market data; no brokerage
            connection.
          </p>
        </div>
        <dl className="app-facts">
          <div>
            <dt>App version</dt>
            <dd>{APP_VERSION}</dd>
          </div>
          <div>
            <dt>API endpoint</dt>
            <dd>{apiBaseUrl}</dd>
          </div>
          <div>
            <dt>Connection</dt>
            <dd>
              <span className={`status status-${state}`}>{state}</span>
            </dd>
          </div>
        </dl>
        <div className="health" role="status" aria-live="polite">
          <strong>/health</strong>
          <span>{message}</span>
          {lastSuccess ? <small>Last success: {lastSuccess}</small> : null}
          <button
            type="button"
            onClick={() => void runCheck()}
            disabled={state === "checking"}
          >
            Retry now
          </button>
        </div>
      </header>
      {lastSuccess ? <Workspace api={api} /> : null}
    </main>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { version as APP_VERSION } from "../package.json";
import { checkApiHealth, type ApiConnectionState } from "./api-health";
import { createCredentialApi } from "./credential-api";
import { AppMark } from "./icons";
import { LANGUAGES, useI18n, type Language, type MessageKey } from "./i18n";
import { I18nProvider } from "./I18nProvider";
import { KeysPanel } from "./KeysPanel";
import { createMarketApi } from "./market-api";
import "./styles.css";
import { Workspace } from "./Workspace";

// checkApiHealth reports these fixed messages; anything else is a raw network error.
const HEALTH_MESSAGES: Record<string, MessageKey> = {
  "Checking API health": "health.checking",
  "API is available": "health.available",
  "Invalid health response": "health.invalid",
};

interface AppProps {
  apiBaseUrl?: string;
  fetcher?: typeof fetch;
  retryMs?: number;
  timeoutMs?: number;
  /** Where the language choice is kept; the browser's localStorage by default. */
  languageStorage?: Storage | null;
}

export function App({ languageStorage, ...props }: AppProps) {
  return (
    <I18nProvider
      {...(languageStorage === undefined ? {} : { storage: languageStorage })}
    >
      <AppContent {...props} />
    </I18nProvider>
  );
}

function AppContent({
  apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
  fetcher = fetch,
  retryMs = 5_000,
  timeoutMs = 5_000,
}: Omit<AppProps, "languageStorage">) {
  const { t, language, setLanguage } = useI18n();
  const [provider, setProvider] = useState<string | null>(null);
  const [state, setState] = useState<ApiConnectionState>("checking");
  const [message, setMessage] = useState("Checking API health");
  const [lastSuccess, setLastSuccess] = useState<string | null>(null);
  const api = useMemo(
    () => createMarketApi(apiBaseUrl, fetcher),
    [apiBaseUrl, fetcher],
  );
  const credentialApi = useMemo(
    () => createCredentialApi(apiBaseUrl, fetcher),
    [apiBaseUrl, fetcher],
  );
  const connected = lastSuccess !== null;
  useEffect(() => {
    if (!connected) return;
    let current = true;
    credentialApi
      .list()
      .then((listing) => current && setProvider(listing.active_provider))
      .catch(() => current && setProvider(null));
    return () => {
      current = false;
    };
  }, [connected, credentialApi]);
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
    <div className="app-shell">
      <div className="app-main">
        {lastSuccess ? (
          <Workspace api={api} keysPanel={<KeysPanel api={credentialApi} />} />
        ) : (
          <div className="connect-screen">
            <AppMark />
            <h1 id="app-title">Stock Intelligence Workstation</h1>
            <p className="subtitle">{t("app.connecting")}</p>
          </div>
        )}
      </div>
      <footer className="status-bar" aria-label={t("status.label")}>
        <strong className="status-title">Stock Intelligence Workstation</strong>
        <span className="status-item">
          {t("app.subtitle", { provider: provider ?? "…" })}
        </span>
        <span className="status-item">
          {t("app.connection")}{" "}
          <span className={`status status-${state}`}>
            {t(`state.${state}`)}
          </span>
        </span>
        <span className="status-item" role="status" aria-live="polite">
          {message in HEALTH_MESSAGES ? t(HEALTH_MESSAGES[message]) : message}
        </span>
        {lastSuccess ? (
          <small className="status-item">
            {t("app.lastSuccess", { time: lastSuccess })}
          </small>
        ) : null}
        <button
          type="button"
          className="tb-button"
          onClick={() => void runCheck()}
          disabled={state === "checking"}
        >
          {t("app.retry")}
        </button>
        <span className="tb-spacer" />
        <span className="status-item">
          {t("app.version")} {APP_VERSION}
        </span>
        <span className="status-item">
          {t("app.endpoint")} {apiBaseUrl}
        </span>
        <label className="status-item status-language">
          {t("app.language")}
          <select
            value={language}
            onChange={(event) => setLanguage(event.target.value as Language)}
          >
            {LANGUAGES.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      </footer>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { version as APP_VERSION } from "../package.json";
import { checkApiHealth, type ApiConnectionState } from "./api-health";
import { createCredentialApi } from "./credential-api";
import { LANGUAGES, useI18n, type Language, type MessageKey } from "./i18n";
import { I18nProvider } from "./I18nProvider";
import { KeysPanel } from "./KeysPanel";
import { createMarketApi } from "./market-api";
import "./styles.css";
import { Workspace } from "./Workspace";

type View = "workspace" | "keys";

const VIEWS: { id: View; label: MessageKey }[] = [
  { id: "workspace", label: "nav.workspace" },
  { id: "keys", label: "nav.keys" },
];

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
  const [view, setView] = useState<View>("workspace");
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
    <div className="shell">
      <nav className="side-nav" aria-label={t("app.menu")}>
        {VIEWS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={
              item.id === view ? "side-nav-item active" : "side-nav-item"
            }
            aria-current={item.id === view ? "page" : undefined}
            onClick={() => setView(item.id)}
          >
            {t(item.label)}
          </button>
        ))}
        <label className="side-nav-language">
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
      </nav>
      <main className="app">
        <header className="app-bar" aria-labelledby="app-title">
          <div>
            <p className="eyebrow">{t("app.eyebrow")}</p>
            <h1 id="app-title">Stock Intelligence Workstation</h1>
            <p className="subtitle">
              {t("app.subtitle", { provider: provider ?? "…" })}
            </p>
          </div>
          <dl className="app-facts">
            <div>
              <dt>{t("app.version")}</dt>
              <dd>{APP_VERSION}</dd>
            </div>
            <div>
              <dt>{t("app.endpoint")}</dt>
              <dd>{apiBaseUrl}</dd>
            </div>
            <div>
              <dt>{t("app.connection")}</dt>
              <dd>
                <span className={`status status-${state}`}>
                  {t(`state.${state}`)}
                </span>
              </dd>
            </div>
          </dl>
          <div className="health" role="status" aria-live="polite">
            <strong>/health</strong>
            <span>
              {message in HEALTH_MESSAGES
                ? t(HEALTH_MESSAGES[message])
                : message}
            </span>
            {lastSuccess ? (
              <small>{t("app.lastSuccess", { time: lastSuccess })}</small>
            ) : null}
            <button
              type="button"
              onClick={() => void runCheck()}
              disabled={state === "checking"}
            >
              {t("app.retry")}
            </button>
          </div>
        </header>
        {lastSuccess ? (
          // Kept mounted while hidden so charts keep their data and scroll position.
          <div hidden={view !== "workspace"}>
            <Workspace api={api} />
          </div>
        ) : null}
        {lastSuccess && view === "keys" ? (
          <KeysPanel api={credentialApi} />
        ) : null}
      </main>
    </div>
  );
}

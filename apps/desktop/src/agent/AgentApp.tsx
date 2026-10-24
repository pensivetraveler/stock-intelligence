import type {
  BacktestStrategy,
  CredentialSummary,
  PaperApproval,
  PaperExecution,
  PaperIntentListResponse,
  PaperRunnerStatus,
} from "@siw/contracts/types";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useI18n, LANGUAGES, type Language } from "../i18n";
import { I18nProvider } from "../I18nProvider";
import "../styles.css";
import { createPaperApi, type PaperApi } from "./paper-api";
import {
  createRunnerControl,
  type RunnerControl,
  type RunnerServiceState,
} from "./runner-control";

type IntentRow = PaperIntentListResponse["intents"][number];

interface AgentAppProps {
  apiBaseUrl?: string;
  paperApi?: PaperApi;
  runnerControl?: RunnerControl;
  pollMs?: number;
  languageStorage?: Storage | null;
  /** The language until the user picks one; SIW Agent opens in Korean. */
  defaultLanguage?: Language;
  /** For tests: the current time. */
  now?: () => Date;
}

/** SIW Agent: paper-trading status, kill switch, approvals and the paper ledger. */
export function AgentApp({
  languageStorage,
  defaultLanguage = "ko",
  ...props
}: AgentAppProps) {
  return (
    <I18nProvider
      fallback={defaultLanguage}
      {...(languageStorage === undefined ? {} : { storage: languageStorage })}
    >
      <AgentScreen {...props} />
    </I18nProvider>
  );
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function AgentScreen({
  apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8765",
  paperApi,
  runnerControl,
  pollMs = 5_000,
  now = () => new Date(),
}: Omit<AgentAppProps, "languageStorage" | "defaultLanguage">) {
  const { t, language, setLanguage } = useI18n();
  useEffect(() => {
    document.title = t("paper.windowTitle");
  }, [t]);
  const api = useMemo(
    () => paperApi ?? createPaperApi(apiBaseUrl),
    [paperApi, apiBaseUrl],
  );
  const control = useMemo(
    () => runnerControl ?? createRunnerControl(),
    [runnerControl],
  );
  const [status, setStatus] = useState<PaperRunnerStatus | null>(null);
  const [approvals, setApprovals] = useState<PaperApproval[]>([]);
  const [intents, setIntents] = useState<IntentRow[]>([]);
  const [executions, setExecutions] = useState<PaperExecution[]>([]);
  const [service, setService] = useState<RunnerServiceState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextApprovals, nextIntents, nextExecutions] =
        await Promise.all([
          api.status(),
          api.approvals(),
          api.intents(),
          api.executions(),
        ]);
      setStatus(nextStatus);
      setApprovals(nextApprovals.approvals);
      setIntents(nextIntents.intents);
      setExecutions(nextExecutions.executions);
      setError(null);
    } catch (caught) {
      setError(message(caught));
    }
    if (control.available) {
      try {
        setService(await control.state());
      } catch (caught) {
        setService({
          installed: false,
          running: false,
          detail: message(caught),
        });
      }
    }
  }, [api, control]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), pollMs);
    return () => window.clearInterval(timer);
  }, [refresh, pollMs]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      setActionError(null);
    } catch (caught) {
      setActionError(message(caught));
    } finally {
      setBusy(false);
      await refresh();
    }
  };

  // The kill switch is recorded through the API; if the API does not answer, the
  // shell stops the runner's LaunchAgent instead (ADR 0037 §5).
  const kill = () =>
    act(async () => {
      try {
        setStatus(await api.kill("kill switch pressed in SIW Agent"));
      } catch (caught) {
        if (!control.available) throw caught;
        setService(await control.stop());
      }
    });

  return (
    <main className="app agent-app">
      <div className="agent-banner" role="note">
        {t("paper.banner")}
      </div>
      <header className="agent-header">
        <h1>{t("paper.title")}</h1>
        <label>
          {t("app.language")}{" "}
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
        <button
          type="button"
          className="agent-kill"
          onClick={() => void kill()}
          disabled={busy}
        >
          {t("paper.kill")}
        </button>
      </header>
      {[error, actionError].map(
        (problem, index) =>
          problem && (
            <p key={index} className="agent-error" role="alert">
              {t("paper.error", { message: problem })}
            </p>
          ),
      )}
      <SummaryCards
        status={status}
        approvals={approvals}
        intents={intents}
        executions={executions}
        busy={busy}
        now={now}
        onRelease={() => act(() => api.release())}
        onDisarm={() => act(() => api.disarm())}
      />
      <ServiceCard
        control={control}
        state={service}
        busy={busy}
        onInstall={() => act(async () => setService(await control.install()))}
        onStop={() => act(async () => setService(await control.stop()))}
        onUninstall={() =>
          act(async () => setService(await control.uninstall()))
        }
      />
      <ApprovalSection
        api={api}
        approvals={approvals}
        status={status}
        busy={busy}
        onArm={(id) => act(() => api.arm(id))}
        onApproved={() => void refresh()}
      />
      <IntentTable rows={intents} />
      <ExecutionTable rows={executions} />
    </main>
  );
}

type Translate = ReturnType<typeof useI18n>["t"];

// A runner that is armed checks in every few seconds; this long a silence is a warning.
const STALE_HEARTBEAT_MS = 2 * 60_000;

/** "just now", "5 min ago", "3 h ago" or the local date and time. */
function ago(t: Translate, iso: string | null, now: Date): string {
  if (iso === null) return t("paper.never");
  const elapsed = now.getTime() - new Date(iso).getTime();
  if (elapsed < 60_000) return t("paper.ago.now");
  if (elapsed < 3_600_000) {
    return t("paper.ago.minutes", { n: Math.floor(elapsed / 60_000) });
  }
  if (elapsed < 86_400_000) {
    return t("paper.ago.hours", { n: Math.floor(elapsed / 3_600_000) });
  }
  return localTime(iso);
}

/** A UTC timestamp in the viewer's time zone, to the second. */
function localTime(iso: string): string {
  const at = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ` +
    `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`
  );
}

/** A decimal amount with thousands separators ("1000000" -> "1,000,000"). */
function grouped(amount: string): string {
  const [whole, fraction] = amount.split(".");
  if (!/^\d+$/.test(whole ?? "")) return amount;
  const withCommas = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return fraction === undefined ? withCommas : `${withCommas}.${fraction}`;
}

function sameLocalDay(iso: string | null, now: Date): boolean {
  if (iso === null) return false;
  const at = new Date(iso);
  return (
    at.getFullYear() === now.getFullYear() &&
    at.getMonth() === now.getMonth() &&
    at.getDate() === now.getDate()
  );
}

function SummaryCards({
  status,
  approvals,
  intents,
  executions,
  busy,
  now,
  onRelease,
  onDisarm,
}: {
  status: PaperRunnerStatus | null;
  approvals: PaperApproval[];
  intents: IntentRow[];
  executions: PaperExecution[];
  busy: boolean;
  now: () => Date;
  onRelease: () => void;
  onDisarm: () => void;
}) {
  const { t } = useI18n();
  if (status === null) {
    return <section className="agent-card">{t("paper.loading")}</section>;
  }
  const at = now();
  const state = status.halted
    ? t("paper.state.halted", { reason: status.halted_reason ?? "" })
    : status.armed
      ? t("paper.state.armed")
      : t("paper.state.disarmed");
  const stale =
    status.armed &&
    status.last_heartbeat_utc !== null &&
    at.getTime() - new Date(status.last_heartbeat_utc).getTime() >
      STALE_HEARTBEAT_MS;
  const armedPlan = approvals.find(
    (item) => status.armed && item.approval_id === status.approval_id,
  );
  const todayIntents = intents.filter((row) =>
    sameLocalDay(row.intent.created_at_utc, at),
  ).length;
  const todayFills = executions.filter((row) =>
    sameLocalDay(row.executed_at_utc, at),
  ).length;
  return (
    <section className="agent-summary" aria-label={t("paper.status")}>
      <div
        className={`agent-tile ${status.halted ? "agent-tile-danger" : "agent-tile-ok"}`}
      >
        <span className="agent-tile-label">{t("paper.summary.kill")}</span>
        <strong>
          {status.halted
            ? t("paper.summary.killOn")
            : t("paper.summary.killOff")}
        </strong>
        {status.halted && (
          <button type="button" onClick={onRelease} disabled={busy}>
            {t("paper.release")}
          </button>
        )}
      </div>
      <div
        className={`agent-tile ${stale ? "agent-tile-warning" : status.armed ? "agent-tile-active" : ""}`}
      >
        <span className="agent-tile-label">{t("paper.status")}</span>
        <strong data-testid="runner-state">{state}</strong>
        <span className="agent-muted">
          {t("paper.heartbeat", {
            time: ago(t, status.last_heartbeat_utc, at),
          })}
        </span>
        {stale && (
          <span className="agent-warning">
            {t("paper.stale", {
              minutes: Math.floor(
                (at.getTime() -
                  new Date(status.last_heartbeat_utc ?? 0).getTime()) /
                  60_000,
              ),
            })}
          </span>
        )}
        {status.armed && (
          <button type="button" onClick={onDisarm} disabled={busy}>
            {t("paper.disarm")}
          </button>
        )}
      </div>
      <div className="agent-tile">
        <span className="agent-tile-label">{t("paper.approvals")}</span>
        <strong>
          {t("paper.summary.plansValue", { count: approvals.length })}
        </strong>
        <span className="agent-muted">
          {armedPlan
            ? t("paper.summary.armedPlan", {
                name: `${armedPlan.strategy.strategy_id}@${armedPlan.strategy.strategy_version}`,
              })
            : t("paper.summary.noneArmed")}
        </span>
      </div>
      <div className="agent-tile">
        <span className="agent-tile-label">{t("paper.summary.today")}</span>
        <strong>
          {t("paper.summary.todayValue", {
            intents: todayIntents,
            fills: todayFills,
          })}
        </strong>
      </div>
      {status.unknown_submission_count > 0 && (
        <p className="agent-warning agent-summary-note">
          {t("paper.unknownOrders", { count: status.unknown_submission_count })}
        </p>
      )}
    </section>
  );
}

function ServiceCard({
  control,
  state,
  busy,
  onInstall,
  onStop,
  onUninstall,
}: {
  control: RunnerControl;
  state: RunnerServiceState | null;
  busy: boolean;
  onInstall: () => void;
  onStop: () => void;
  onUninstall: () => void;
}) {
  const { t } = useI18n();
  return (
    <section className="agent-card" aria-label={t("paper.service")}>
      <h2>{t("paper.service")}</h2>
      {!control.available ? (
        <p>{t("paper.service.unavailable")}</p>
      ) : (
        <>
          <p>
            {state === null
              ? t("paper.loading")
              : state.running
                ? t("paper.service.running")
                : state.installed
                  ? t("paper.service.stopped")
                  : t("paper.service.notInstalled")}
          </p>
          {state?.detail && <p className="agent-muted">{state.detail}</p>}
          <button type="button" onClick={onInstall} disabled={busy}>
            {t("paper.service.install")}
          </button>
          <button type="button" onClick={onStop} disabled={busy}>
            {t("paper.service.stop")}
          </button>
          <button type="button" onClick={onUninstall} disabled={busy}>
            {t("paper.service.uninstall")}
          </button>
        </>
      )}
    </section>
  );
}

interface ChosenInstrument {
  instrument_id: string;
  symbol: string;
  name: string;
}

function ApprovalSection({
  api,
  approvals,
  status,
  busy,
  onArm,
  onApproved,
}: {
  api: PaperApi;
  approvals: PaperApproval[];
  status: PaperRunnerStatus | null;
  busy: boolean;
  onArm: (approvalId: string) => void;
  onApproved: () => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <section className="agent-card" aria-label={t("paper.approvals")}>
      <div className="agent-card-head">
        <h2>{t("paper.approvals")}</h2>
        {!open && (
          <button type="button" onClick={() => setOpen(true)}>
            {t("paper.wizard.open")}
          </button>
        )}
      </div>
      {approvals.length === 0 ? (
        <p className="agent-muted">{t("paper.approvals.empty")}</p>
      ) : (
        <ul className="agent-list">
          {approvals.map((approval) => (
            <li key={approval.approval_id}>
              <span>
                <strong>
                  {approval.strategy.strategy_id}@
                  {approval.strategy.strategy_version}
                </strong>{" "}
                · {approval.instruments.map((item) => item.symbol).join(", ")} ·{" "}
                {t("paper.limitAmount", {
                  amount:
                    approval.limits.max_order_amount_krw === null
                      ? t("paper.noLimit")
                      : grouped(approval.limits.max_order_amount_krw),
                })}
              </span>
              {status?.approval_id === approval.approval_id && status.armed ? (
                <strong className="agent-armed">{t("paper.armedHere")}</strong>
              ) : (
                <button
                  type="button"
                  onClick={() => onArm(approval.approval_id)}
                  disabled={busy || status?.halted === true}
                >
                  {t("paper.arm")}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {open && (
        <PlanWizard
          api={api}
          busy={busy}
          onCancel={() => setOpen(false)}
          onApproved={() => {
            setOpen(false);
            onApproved();
          }}
        />
      )}
    </section>
  );
}

const STEPS = 4;

function PlanWizard({
  api,
  busy,
  onCancel,
  onApproved,
}: {
  api: PaperApi;
  busy: boolean;
  onCancel: () => void;
  onApproved: () => void;
}) {
  const { t } = useI18n();
  const [step, setStep] = useState(1);
  const [strategies, setStrategies] = useState<BacktestStrategy[]>([]);
  const [keys, setKeys] = useState<CredentialSummary[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [credentialId, setCredentialId] = useState("");
  const [chosen, setChosen] = useState<ChosenInstrument[]>([]);
  const [amount, setAmount] = useState("1000000");
  const [rate, setRate] = useState("");
  const [fxMode, setFxMode] = useState<"fixed" | "bok_ecos">("bok_ecos");
  const [equity, setEquity] = useState("");
  const [buffer, setBuffer] = useState("50");
  const [settle, setSettle] = useState("60");
  const [maxAge, setMaxAge] = useState("300");
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [list, credentials] = await Promise.all([
          api.strategies(),
          api.credentials(),
        ]);
        setStrategies(list.strategies);
        setKeys(
          credentials.credentials.filter(
            (key) =>
              key.provider === "kiwoom" &&
              key.environment === "demo" &&
              key.enabled,
          ),
        );
      } catch (caught) {
        setFormError(message(caught));
      }
    })();
  }, [api]);

  const strategy = strategies.find((item) => item.strategy_id === strategyId);
  const key = keys.find((item) => item.credential_id === credentialId);
  const problem = (at: number): string | null => {
    if (at === 1 && (!strategy || !key)) return t("paper.wizard.missing1");
    if (at === 2 && chosen.length === 0) return t("paper.wizard.missing2");
    if (at === 3 && fxMode === "fixed" && rate.trim() === "") {
      return t("paper.wizard.missing3");
    }
    return null;
  };
  const next = () => {
    const found = problem(step);
    setFormError(found);
    if (found === null) setStep(step + 1);
  };

  const submit = async () => {
    if (!strategy || !key || chosen.length === 0) {
      setFormError(t("paper.approve.missing"));
      return;
    }
    try {
      await api.approve({
        strategy_id: strategy.strategy_id,
        strategy_version: strategy.version,
        instruments: chosen.map(({ instrument_id, symbol }) => ({
          instrument_id,
          symbol,
        })),
        credential_id: key.credential_id,
        max_order_amount_krw: amount.trim() === "" ? null : amount.trim(),
        market_buffer_bps: buffer.trim() || "50",
        krw_per_usd:
          fxMode === "bok_ecos" || rate.trim() === "" ? null : rate.trim(),
        fx_source:
          fxMode === "bok_ecos" || rate.trim() === ""
            ? null
            : "entered in SIW Agent",
        fx_mode: fxMode,
        equity_basis: equity.trim() === "" ? null : equity.trim(),
        bar_settle_seconds: settle.trim() === "" ? null : Number(settle.trim()),
        max_signal_age_seconds: Number(maxAge.trim() || "300"),
      });
      setFormError(null);
      onApproved();
    } catch (caught) {
      setFormError(message(caught));
    }
  };

  const titles = [
    t("paper.wizard.s1"),
    t("paper.wizard.s2"),
    t("paper.wizard.s3"),
    t("paper.wizard.s4"),
  ];
  return (
    <div
      className="agent-wizard"
      role="group"
      aria-label={t("paper.approve.title")}
    >
      <ol className="agent-steps">
        {titles.map((title, index) => (
          <li
            key={title}
            className={
              index + 1 === step
                ? "agent-step-current"
                : index + 1 < step
                  ? "agent-step-done"
                  : ""
            }
            aria-current={index + 1 === step ? "step" : undefined}
          >
            {index + 1}. {title}
          </li>
        ))}
      </ol>
      {step === 1 && (
        <div className="agent-form">
          <label>
            {t("paper.approve.strategy")}
            <select
              value={strategyId}
              onChange={(event) => setStrategyId(event.target.value)}
            >
              <option value="">{t("paper.wizard.choose")}</option>
              {strategies.map((item) => (
                <option key={item.strategy_id} value={item.strategy_id}>
                  {item.name} (v{item.version})
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("paper.approve.key")}
            <select
              value={credentialId}
              onChange={(event) => setCredentialId(event.target.value)}
            >
              <option value="">{t("paper.wizard.choose")}</option>
              {keys.map((item) => (
                <option key={item.credential_id} value={item.credential_id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      {step === 2 && (
        <SymbolPicker api={api} chosen={chosen} onChange={setChosen} />
      )}
      {step === 3 && (
        <div className="agent-form">
          <label>
            {t("paper.approve.amount")}
            <input
              value={amount}
              inputMode="numeric"
              onChange={(event) => setAmount(event.target.value)}
            />
          </label>
          <label>
            {t("paper.approve.fxMode")}
            <select
              value={fxMode}
              onChange={(event) =>
                setFxMode(event.target.value as "fixed" | "bok_ecos")
              }
            >
              <option value="bok_ecos">{t("paper.approve.fxEcos")}</option>
              <option value="fixed">{t("paper.approve.fxFixed")}</option>
            </select>
          </label>
          {fxMode === "fixed" && (
            <label>
              {t("paper.approve.rate")}
              <input
                value={rate}
                inputMode="decimal"
                onChange={(event) => setRate(event.target.value)}
              />
            </label>
          )}
          <details className="agent-advanced">
            <summary>{t("paper.wizard.advanced")}</summary>
            <label>
              {t("paper.approve.buffer")}
              <input
                value={buffer}
                onChange={(event) => setBuffer(event.target.value)}
              />
            </label>
            <label>
              {t("paper.approve.equity")}
              <input
                value={equity}
                onChange={(event) => setEquity(event.target.value)}
              />
            </label>
            <label>
              {t("paper.approve.settle")}
              <input
                value={settle}
                onChange={(event) => setSettle(event.target.value)}
              />
            </label>
            <label>
              {t("paper.approve.maxAge")}
              <input
                value={maxAge}
                onChange={(event) => setMaxAge(event.target.value)}
              />
            </label>
          </details>
          <p className="agent-muted">{t("paper.approve.note")}</p>
        </div>
      )}
      {step === 4 && (
        <dl className="agent-review">
          <dt>{t("paper.approve.strategy")}</dt>
          <dd>
            {strategy?.name} (v{strategy?.version})
          </dd>
          <dt>{t("paper.approve.key")}</dt>
          <dd>{key?.label}</dd>
          <dt>{t("paper.wizard.s2")}</dt>
          <dd>{chosen.map((item) => item.symbol).join(", ")}</dd>
          <dt>{t("paper.approve.amount")}</dt>
          <dd>
            {amount.trim() === ""
              ? t("paper.noLimit")
              : t("paper.wizard.won", { amount: grouped(amount.trim()) })}
          </dd>
          <dt>{t("paper.approve.fxMode")}</dt>
          <dd>
            {fxMode === "bok_ecos"
              ? t("paper.approve.fxEcos")
              : t("paper.wizard.fixedRate", { rate: rate.trim() })}
          </dd>
          <dt>{t("paper.wizard.advanced")}</dt>
          <dd>
            {t("paper.wizard.advancedSummary", {
              buffer: buffer.trim() || "50",
              settle: settle.trim() || "—",
              age: maxAge.trim() || "300",
            })}
          </dd>
        </dl>
      )}
      {formError && (
        <p className="agent-error" role="alert">
          {formError}
        </p>
      )}
      <div className="agent-wizard-actions">
        <button type="button" onClick={onCancel}>
          {t("paper.wizard.cancel")}
        </button>
        {step > 1 && (
          <button
            type="button"
            onClick={() => {
              setFormError(null);
              setStep(step - 1);
            }}
          >
            {t("paper.wizard.back")}
          </button>
        )}
        {step < STEPS ? (
          <button type="button" className="agent-primary" onClick={next}>
            {t("paper.wizard.next")}
          </button>
        ) : (
          <button
            type="button"
            className="agent-primary"
            onClick={() => void submit()}
            disabled={busy}
          >
            {t("paper.approve.submit")}
          </button>
        )}
      </div>
    </div>
  );
}

/** Search listings by ticker or name and keep the chosen ones (a plan's instruments). */
function SymbolPicker({
  api,
  chosen,
  onChange,
}: {
  api: PaperApi;
  chosen: ChosenInstrument[];
  onChange: (chosen: ChosenInstrument[]) => void;
}) {
  const { t, language } = useI18n();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ChosenInstrument[] | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  useEffect(() => {
    const text = query.trim();
    if (text === "") {
      setResults(null);
      return;
    }
    let current = true;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const found = await api.searchSymbols(text);
          if (!current) return;
          setResults(
            found.results.map((item) => ({
              instrument_id: item.listing.instrument.instrument_id,
              symbol: item.listing.instrument.symbol,
              name: (language === "ko" ? item.local_name : null) ?? item.name,
            })),
          );
          setSearchError(null);
        } catch (caught) {
          if (current) setSearchError(message(caught));
        }
      })();
    }, 250);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [api, query, language]);
  const has = (id: string) => chosen.some((item) => item.instrument_id === id);
  return (
    <div className="agent-form">
      <label>
        {t("paper.symbols.search")}
        <input
          type="search"
          value={query}
          placeholder="AAPL, Apple, 애플"
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {searchError && <p className="agent-error">{searchError}</p>}
      {results !== null &&
        (results.length === 0 ? (
          <p className="agent-muted">{t("paper.symbols.none")}</p>
        ) : (
          <ul className="agent-results" aria-label={t("paper.symbols.results")}>
            {results.map((item) => (
              <li key={item.instrument_id}>
                <button
                  type="button"
                  disabled={has(item.instrument_id)}
                  onClick={() => onChange([...chosen, item])}
                >
                  <strong>{item.symbol}</strong> {item.name}
                </button>
              </li>
            ))}
          </ul>
        ))}
      <div className="agent-chips" aria-label={t("paper.symbols.chosen")}>
        {chosen.length === 0 ? (
          <span className="agent-muted">{t("paper.symbols.empty")}</span>
        ) : (
          chosen.map((item) => (
            <span key={item.instrument_id} className="agent-chip">
              {item.symbol}
              <button
                type="button"
                aria-label={t("paper.symbols.remove", { symbol: item.symbol })}
                onClick={() =>
                  onChange(
                    chosen.filter(
                      (other) => other.instrument_id !== item.instrument_id,
                    ),
                  )
                }
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>
    </div>
  );
}

function sideLabel(t: Translate, side: string): string {
  return side === "buy"
    ? t("paper.side.buy")
    : side === "sell"
      ? t("paper.side.sell")
      : side;
}

function IntentTable({ rows }: { rows: IntentRow[] }) {
  const { t } = useI18n();
  return (
    <section className="agent-card" aria-label={t("paper.intents")}>
      <h2>{t("paper.intents")}</h2>
      {rows.length === 0 ? (
        <p className="agent-muted">{t("paper.intents.empty")}</p>
      ) : (
        <table className="agent-table">
          <thead>
            <tr>
              <th>{t("paper.col.time")}</th>
              <th>{t("paper.col.symbol")}</th>
              <th>{t("paper.col.side")}</th>
              <th>{t("paper.col.order")}</th>
              <th>{t("paper.col.check")}</th>
              <th>{t("paper.col.outcome")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ intent, outcome, submission }) => (
              <tr key={intent.intent_id}>
                <td>{localTime(intent.created_at_utc)}</td>
                <td>{intent.symbol}</td>
                <td>{sideLabel(t, intent.side)}</td>
                <td>
                  {intent.order.order_type} {intent.order.quantity} @{" "}
                  {intent.order.limit_price ??
                    `≤${intent.order.estimated_max_price ?? "?"}`}
                </td>
                <td>
                  {intent.limit_check.passed
                    ? t("paper.check.passed")
                    : intent.limit_check.reasons.join(", ")}
                </td>
                <td>
                  {outcome
                    ? `${outcome.status}${outcome.reason ? ` (${outcome.reason})` : ""}`
                    : "—"}
                  {submission && ` · ${submission.status}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function ExecutionTable({ rows }: { rows: PaperExecution[] }) {
  const { t } = useI18n();
  return (
    <section className="agent-card" aria-label={t("paper.executions")}>
      <h2>{t("paper.executions")}</h2>
      <p className="agent-muted">{t("paper.executions.note")}</p>
      {rows.length === 0 ? (
        <p className="agent-muted">{t("paper.executions.empty")}</p>
      ) : (
        <table className="agent-table">
          <thead>
            <tr>
              <th>{t("paper.col.time")}</th>
              <th>{t("paper.col.symbol")}</th>
              <th>{t("paper.col.side")}</th>
              <th>{t("paper.col.fill")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.execution_id}>
                <td>
                  {row.executed_at_utc
                    ? localTime(row.executed_at_utc)
                    : t("paper.timeUnresolved")}
                </td>
                <td>{row.symbol}</td>
                <td>{sideLabel(t, row.side)}</td>
                <td>
                  {row.quantity} @ {row.price} {row.currency}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

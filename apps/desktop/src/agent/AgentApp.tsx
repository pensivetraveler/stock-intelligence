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
}

/** SIW Agent: paper-trading status, kill switch, approvals and the paper ledger. */
export function AgentApp({ languageStorage, ...props }: AgentAppProps) {
  return (
    <I18nProvider
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
}: Omit<AgentAppProps, "languageStorage">) {
  const { t, language, setLanguage } = useI18n();
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
      <StatusCard
        status={status}
        busy={busy}
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

function StatusCard({
  status,
  busy,
  onRelease,
  onDisarm,
}: {
  status: PaperRunnerStatus | null;
  busy: boolean;
  onRelease: () => void;
  onDisarm: () => void;
}) {
  const { t } = useI18n();
  if (status === null) {
    return <section className="agent-card">{t("paper.loading")}</section>;
  }
  const state = status.halted
    ? t("paper.state.halted", { reason: status.halted_reason ?? "" })
    : status.armed
      ? t("paper.state.armed")
      : t("paper.state.disarmed");
  return (
    <section className="agent-card" aria-label={t("paper.status")}>
      <h2>{t("paper.status")}</h2>
      <p data-testid="runner-state">{state}</p>
      <p>
        {t("paper.heartbeat", {
          time: status.last_heartbeat_utc ?? t("paper.never"),
        })}
      </p>
      {status.unknown_submission_count > 0 && (
        <p className="agent-warning">
          {t("paper.unknownOrders", { count: status.unknown_submission_count })}
        </p>
      )}
      {status.halted && (
        <button type="button" onClick={onRelease} disabled={busy}>
          {t("paper.release")}
        </button>
      )}
      {status.armed && (
        <button type="button" onClick={onDisarm} disabled={busy}>
          {t("paper.disarm")}
        </button>
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
  const [strategies, setStrategies] = useState<BacktestStrategy[]>([]);
  const [keys, setKeys] = useState<CredentialSummary[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [credentialId, setCredentialId] = useState("");
  const [instruments, setInstruments] = useState("us:aapl:xnas:2026 AAPL");
  const [amount, setAmount] = useState("1000000");
  const [rate, setRate] = useState("");
  const [fxMode, setFxMode] = useState<"fixed" | "bok_ecos">("bok_ecos");
  const [equity, setEquity] = useState("");
  const [buffer, setBuffer] = useState("50");
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

  const submit = async () => {
    const strategy = strategies.find((item) => item.strategy_id === strategyId);
    const pairs = instruments
      .split(/[\n,]+/)
      .map((line) => line.trim().split(/\s+/))
      .filter((parts) => parts.length === 2 && parts[0] !== "");
    if (!strategy || !credentialId || pairs.length === 0) {
      setFormError(t("paper.approve.missing"));
      return;
    }
    try {
      await api.approve({
        strategy_id: strategy.strategy_id,
        strategy_version: strategy.version,
        instruments: pairs.map(([instrument_id, symbol]) => ({
          instrument_id,
          symbol,
        })),
        credential_id: credentialId,
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
      });
      setFormError(null);
      onApproved();
    } catch (caught) {
      setFormError(message(caught));
    }
  };

  return (
    <section className="agent-card" aria-label={t("paper.approvals")}>
      <h2>{t("paper.approvals")}</h2>
      <ul className="agent-list">
        {approvals.map((approval) => (
          <li key={approval.approval_id}>
            <span>
              {approval.strategy.strategy_id}@
              {approval.strategy.strategy_version} ·{" "}
              {approval.instruments.map((item) => item.symbol).join(", ")} ·{" "}
              {t("paper.limitAmount", {
                amount:
                  approval.limits.max_order_amount_krw ?? t("paper.noLimit"),
              })}
            </span>
            {status?.approval_id === approval.approval_id && status.armed ? (
              <strong>{t("paper.armedHere")}</strong>
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
      <fieldset className="agent-form">
        <legend>{t("paper.approve.title")}</legend>
        <label>
          {t("paper.approve.strategy")}
          <select
            value={strategyId}
            onChange={(event) => setStrategyId(event.target.value)}
          >
            <option value="">—</option>
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
            <option value="">—</option>
            {keys.map((key) => (
              <option key={key.credential_id} value={key.credential_id}>
                {key.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("paper.approve.instruments")}
          <textarea
            value={instruments}
            onChange={(event) => setInstruments(event.target.value)}
            rows={2}
          />
        </label>
        <label>
          {t("paper.approve.amount")}
          <input
            value={amount}
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
              onChange={(event) => setRate(event.target.value)}
            />
          </label>
        )}
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
        <p className="agent-muted">{t("paper.approve.note")}</p>
        {formError && (
          <p className="agent-error" role="alert">
            {formError}
          </p>
        )}
        <button type="button" onClick={() => void submit()} disabled={busy}>
          {t("paper.approve.submit")}
        </button>
      </fieldset>
    </section>
  );
}

function IntentTable({ rows }: { rows: IntentRow[] }) {
  const { t } = useI18n();
  return (
    <section className="agent-card" aria-label={t("paper.intents")}>
      <h2>{t("paper.intents")}</h2>
      {rows.length === 0 ? (
        <p>{t("paper.none")}</p>
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
                <td>{intent.created_at_utc}</td>
                <td>{intent.symbol}</td>
                <td>{intent.side}</td>
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
        <p>{t("paper.none")}</p>
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
                <td>{row.executed_at_utc ?? t("paper.timeUnresolved")}</td>
                <td>{row.symbol}</td>
                <td>{row.side}</td>
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

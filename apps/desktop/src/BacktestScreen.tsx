import type {
  BacktestFill,
  BacktestResults,
  BacktestRun,
  BacktestStrategy,
} from "@siw/contracts/types";
import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  BacktestApi,
  RunConfigRequest,
  StrategyDefinition,
} from "./backtest-api";
import {
  defaultRulesDraft,
  percent,
  PYTHON_TEMPLATE,
  rulesDefinition,
  rulesDraftOf,
  type RulesDraft,
} from "./backtest-model";
import type { AgentApi } from "./agent-api";
import { BacktestResultsView, type RealFills } from "./BacktestResultsView";
import { translateDynamic, useI18n } from "./i18n";
import type { InstrumentListing } from "./market-api";
import { RuleBuilder } from "./RuleBuilder";

const POLL_MS = 1_500;
const ADJUSTMENT_CHOICES = ["split_adjusted", "fully_adjusted", "raw"] as const;
type Adjustment = (typeof ADJUSTMENT_CHOICES)[number];
const ACTIVE = new Set(["queued", "running"]);

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function currencyOf(listing: InstrumentListing | undefined, id: string) {
  return listing?.instrument.currency ?? (id.startsWith("kr:") ? "KRW" : "USD");
}

interface Editor {
  strategyId: string | null;
  name: string;
  type: "rules" | "python";
  rules: RulesDraft;
  /** A saved rules definition too deep for the form: shown, run as saved. */
  savedRules: Record<string, unknown> | null;
  code: string;
  params: string;
}

const NEW_EDITOR = (): Editor => ({
  strategyId: null,
  name: "EMA 10 / SMA 30",
  type: "rules",
  rules: defaultRulesDraft(),
  savedRules: null,
  code: PYTHON_TEMPLATE,
  params: '{"length": 14}',
});

function editorOf(strategy: BacktestStrategy): Editor {
  const definition = strategy.definition as Record<string, unknown>;
  const base = NEW_EDITOR();
  if (strategy.strategy_type === "python") {
    return {
      ...base,
      strategyId: strategy.strategy_id,
      name: strategy.name,
      type: "python",
      code: String(definition.code ?? ""),
      params: JSON.stringify(definition.params ?? {}),
    };
  }
  const draft = rulesDraftOf(definition);
  return {
    ...base,
    strategyId: strategy.strategy_id,
    name: strategy.name,
    type: "rules",
    rules: draft ?? base.rules,
    savedRules: draft ? null : definition,
  };
}

/**
 * Backtests (ADR 0021): write a rules or Python strategy, run it over chosen
 * instruments on the API's queue, and read the results. Backtest fills stay in the
 * backtest store; showing them on the charts never adds them to the journal.
 */
export function BacktestScreen({
  api,
  agentApi,
  instruments,
  activeInstrumentId,
  real,
  shownRunId,
  onShowFills,
  onClose,
}: {
  api: BacktestApi;
  /** When given, a finished run can be critiqued by the review Agent. */
  agentApi?: AgentApi;
  instruments: InstrumentListing[];
  activeInstrumentId: string;
  real: RealFills;
  shownRunId: string | null;
  onShowFills: (shown: { runId: string; fills: BacktestFill[] } | null) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [strategies, setStrategies] = useState<BacktestStrategy[]>([]);
  const [editor, setEditor] = useState<Editor>(NEW_EDITOR);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [results, setResults] = useState<{
    runId: string;
    results: BacktestResults;
  } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const today = new Date();
  const [config, setConfig] = useState({
    instrumentIds: [activeInstrumentId],
    timeframe: "1d",
    startDate: isoDate(
      new Date(Date.UTC(today.getUTCFullYear() - 2, today.getUTCMonth(), 1)),
    ),
    endDate: isoDate(today),
    // "count": the first barCount bars from startDate, searched up to today.
    rangeMode: "dates" as "dates" | "count",
    barCount: "5000",
    capital: activeInstrumentId.startsWith("kr:") ? "10000000" : "100000",
    feeRate: "0.0005",
    feePerOrder: "0",
    slippageBps: "5",
    adjustment: "split_adjusted" as Adjustment,
  });

  const message = useCallback(
    (error: unknown) =>
      error instanceof Error ? error.message : t("common.requestFailed"),
    [t],
  );

  const loadStrategies = useCallback(() => {
    api
      .strategies()
      .then(setStrategies)
      .catch((error: unknown) => setProblem(message(error)));
  }, [api, message]);
  const loadRuns = useCallback(() => {
    api
      .runs()
      .then(setRuns)
      .catch((error: unknown) => setProblem(message(error)));
  }, [api, message]);

  useEffect(() => {
    loadStrategies();
    loadRuns();
  }, [loadStrategies, loadRuns]);

  // Escape closes the screen even when focus is not inside it.
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const anyActive = runs.some((run) => ACTIVE.has(run.status));
  useEffect(() => {
    if (!anyActive) return;
    const timer = window.setInterval(loadRuns, POLL_MS);
    return () => window.clearInterval(timer);
  }, [anyActive, loadRuns]);

  const selectedRun = runs.find((run) => run.run_id === selectedRunId) ?? null;
  useEffect(() => {
    if (!selectedRun || selectedRun.status !== "succeeded") return;
    if (results?.runId === selectedRun.run_id) return;
    let current = true;
    api
      .results(selectedRun.run_id)
      .then(
        (loaded) =>
          current && setResults({ runId: selectedRun.run_id, results: loaded }),
      )
      .catch((error: unknown) => current && setProblem(message(error)));
    return () => {
      current = false;
    };
  }, [api, selectedRun, results, message]);

  const listing = useMemo(
    () =>
      new Map(instruments.map((item) => [item.instrument.instrument_id, item])),
    [instruments],
  );
  const symbolOf = (id: string) => listing.get(id)?.instrument.symbol ?? id;
  // Only instruments shown as checkboxes are run, so nothing hidden is included.
  const chosen = config.instrumentIds.filter((id) => listing.has(id));
  const currencies = new Set(
    chosen.map((id) => currencyOf(listing.get(id), id)),
  );
  const currency = currencies.size === 1 ? [...currencies][0] : null;
  // Adjusted daily bars: Alpaca for US runs, Toss for Korean runs (splits and other
  // capital changes only, no dividends). Daily and weekly runs only.
  const canAdjust =
    currency !== null &&
    (config.timeframe === "1d" || config.timeframe === "1w");
  const adjustmentChoices = ADJUSTMENT_CHOICES.filter(
    (choice) => currency !== "KRW" || choice !== "fully_adjusted",
  );
  const adjustment: Adjustment = !canAdjust
    ? "raw"
    : adjustmentChoices.includes(config.adjustment)
      ? config.adjustment
      : "split_adjusted";
  const strategyNames = new Map(
    strategies.map((item) => [item.strategy_id, item.name]),
  );

  const definition = (): StrategyDefinition => {
    if (editor.type === "rules") {
      return editor.savedRules
        ? (editor.savedRules as StrategyDefinition)
        : rulesDefinition(editor.rules);
    }
    let params: unknown;
    try {
      params = JSON.parse(editor.params || "{}");
    } catch {
      throw new Error(t("bt.paramsInvalid"));
    }
    return { type: "python", code: editor.code, params };
  };

  /** Saves the editor (a new version only when it changed), then queues a run of it. */
  const saveAndRun = async (run: boolean) => {
    setProblem(null);
    setBusy(true);
    try {
      const saved = await api.saveStrategy(
        editor.name.trim() || t("bt.untitled"),
        definition(),
        editor.strategyId ?? undefined,
      );
      setEditor((current) => ({ ...current, strategyId: saved.strategy_id }));
      loadStrategies();
      if (run) {
        if (!currency) throw new Error(t("bt.mixedCurrency"));
        const barCount = Number(config.barCount);
        if (
          config.rangeMode === "count" &&
          !(Number.isInteger(barCount) && barCount >= 1 && barCount <= 5000)
        ) {
          throw new Error(t("bt.barCountInvalid"));
        }
        const request: RunConfigRequest = {
          instrument_ids: chosen,
          timeframe: config.timeframe,
          start_date: config.startDate,
          end_date:
            config.rangeMode === "count" ? isoDate(today) : config.endDate,
          ...(config.rangeMode === "count" ? { bar_count: barCount } : {}),
          initial_capital: config.capital.trim(),
          currency: currency as RunConfigRequest["currency"],
          fee_rate: config.feeRate.trim() || "0",
          fee_per_order: config.feePerOrder.trim() || "0",
          slippage_bps: config.slippageBps.trim() || "0",
          adjustment,
          seed: 0,
        };
        const queued = await api.submit(
          saved.strategy_id,
          saved.version,
          request,
        );
        setRuns((current) => [queued, ...current]);
        setSelectedRunId(queued.run_id);
      }
    } catch (error) {
      setProblem(message(error));
    } finally {
      setBusy(false);
    }
  };

  const cancel = (runId: string) =>
    api
      .cancel(runId)
      .then(loadRuns)
      .catch((error: unknown) => setProblem(message(error)));

  const toggle = (id: string) =>
    setConfig((current) => ({
      ...current,
      instrumentIds: current.instrumentIds.includes(id)
        ? current.instrumentIds.filter((item) => item !== id)
        : [...current.instrumentIds, id],
    }));

  const status = (run: BacktestRun) =>
    translateDynamic(t, `bt.reason.${run.status}`, run.status) +
    (run.status === "running" ? ` ${Math.round(run.progress * 100)}%` : "");

  return (
    <div
      className="modal-backdrop bt-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal bt-screen"
        role="dialog"
        aria-label={t("bt.title")}
      >
        <header className="modal-head">
          <h2>{t("bt.title")}</h2>
          <span className="workspace-note">{t("bt.subtitle")}</span>
          <button
            type="button"
            className="tb-button"
            aria-label={t("bt.close")}
            onClick={onClose}
          >
            ✕
          </button>
        </header>
        {problem ? (
          <p className="bt-problem" role="alert">
            {problem}
          </p>
        ) : null}
        <div className="bt-body">
          <div className="bt-column">
            <h3>{t("bt.strategy")}</h3>
            <div className="bt-grid">
              <label>
                {t("bt.saved")}
                <select
                  value={editor.strategyId ?? ""}
                  onChange={(event) => {
                    const picked = strategies.find(
                      (item) => item.strategy_id === event.target.value,
                    );
                    setEditor(picked ? editorOf(picked) : NEW_EDITOR());
                  }}
                >
                  <option value="">{t("bt.newStrategy")}</option>
                  {strategies.map((item) => (
                    <option key={item.strategy_id} value={item.strategy_id}>
                      {item.name} · v{item.version}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("bt.name")}
                <input
                  value={editor.name}
                  maxLength={120}
                  onChange={(event) =>
                    setEditor({ ...editor, name: event.target.value })
                  }
                />
              </label>
              <label>
                {t("bt.type")}
                <select
                  value={editor.type}
                  onChange={(event) =>
                    setEditor({
                      ...editor,
                      type: event.target.value as Editor["type"],
                    })
                  }
                >
                  <option value="rules">{t("bt.type.rules")}</option>
                  <option value="python">{t("bt.type.python")}</option>
                </select>
              </label>
            </div>
            {editor.type === "rules" && editor.savedRules ? (
              <>
                <p className="workspace-note">{t("bt.tooDeep")}</p>
                <pre className="bt-logs">
                  {JSON.stringify(editor.savedRules, null, 2)}
                </pre>
              </>
            ) : null}
            {editor.type === "rules" && !editor.savedRules ? (
              <RuleBuilder
                draft={editor.rules}
                onChange={(rules) => setEditor({ ...editor, rules })}
              />
            ) : null}
            {editor.type === "python" ? (
              <>
                <p className="workspace-note">{t("bt.pythonNote")}</p>
                <textarea
                  className="bt-code"
                  aria-label={t("bt.code")}
                  spellCheck={false}
                  value={editor.code}
                  onChange={(event) =>
                    setEditor({ ...editor, code: event.target.value })
                  }
                />
                <label className="bt-inline">
                  {t("bt.params")}
                  <input
                    value={editor.params}
                    onChange={(event) =>
                      setEditor({ ...editor, params: event.target.value })
                    }
                  />
                </label>
              </>
            ) : null}

            <h3>{t("bt.run")}</h3>
            <fieldset className="bt-instruments">
              <legend>{t("bt.instruments")}</legend>
              {instruments.map((item) => {
                const id = item.instrument.instrument_id;
                return (
                  <label key={id} className="bt-inline">
                    <input
                      type="checkbox"
                      checked={config.instrumentIds.includes(id)}
                      onChange={() => toggle(id)}
                    />
                    {item.instrument.symbol}
                  </label>
                );
              })}
            </fieldset>
            {currencies.size > 1 ? (
              <p className="bt-problem">{t("bt.mixedCurrency")}</p>
            ) : null}
            <div className="bt-grid">
              <label>
                {t("bt.timeframe")}
                <select
                  value={config.timeframe}
                  onChange={(event) =>
                    setConfig({ ...config, timeframe: event.target.value })
                  }
                >
                  {["1d", "1w", "60m", "30m", "15m", "5m", "1m"].map((tf) => (
                    <option key={tf} value={tf}>
                      {tf}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("bt.start")}
                <input
                  type="date"
                  value={config.startDate}
                  onChange={(event) =>
                    setConfig({ ...config, startDate: event.target.value })
                  }
                />
              </label>
              <label>
                {t("bt.rangeMode")}
                <select
                  value={config.rangeMode}
                  onChange={(event) =>
                    setConfig({
                      ...config,
                      rangeMode: event.target.value as "dates" | "count",
                    })
                  }
                >
                  <option value="dates">{t("bt.rangeDates")}</option>
                  <option value="count">{t("bt.rangeCount")}</option>
                </select>
              </label>
              {config.rangeMode === "count" ? (
                <label>
                  {t("bt.barCount")}
                  <input
                    type="number"
                    min={1}
                    max={5000}
                    step={1}
                    value={config.barCount}
                    onChange={(event) =>
                      setConfig({ ...config, barCount: event.target.value })
                    }
                  />
                </label>
              ) : (
                <label>
                  {t("bt.end")}
                  <input
                    type="date"
                    value={config.endDate}
                    onChange={(event) =>
                      setConfig({ ...config, endDate: event.target.value })
                    }
                  />
                </label>
              )}
              <label>
                {t("bt.capital", { currency: currency ?? "—" })}
                <input
                  inputMode="decimal"
                  value={config.capital}
                  onChange={(event) =>
                    setConfig({ ...config, capital: event.target.value })
                  }
                />
              </label>
              <label>
                {t("bt.feeRate")}
                <input
                  inputMode="decimal"
                  value={config.feeRate}
                  onChange={(event) =>
                    setConfig({ ...config, feeRate: event.target.value })
                  }
                />
              </label>
              <label>
                {t("bt.feePerOrder")}
                <input
                  inputMode="decimal"
                  value={config.feePerOrder}
                  onChange={(event) =>
                    setConfig({ ...config, feePerOrder: event.target.value })
                  }
                />
              </label>
              <label>
                {t("bt.slippage")}
                <input
                  inputMode="decimal"
                  value={config.slippageBps}
                  onChange={(event) =>
                    setConfig({ ...config, slippageBps: event.target.value })
                  }
                />
              </label>
              <label>
                {t("bt.adjustment")}
                <select
                  value={adjustment}
                  disabled={!canAdjust}
                  onChange={(event) =>
                    setConfig({
                      ...config,
                      adjustment: event.target.value as Adjustment,
                    })
                  }
                >
                  {adjustmentChoices.map((choice) => (
                    <option key={choice} value={choice}>
                      {t(`bt.adj.${choice}`)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="workspace-note">
              {!canAdjust
                ? t("bt.adjRawOnly")
                : currency === "KRW"
                  ? t("bt.adjNoteKr")
                  : t("bt.adjNote")}
            </p>
            {config.timeframe !== "1d" && config.timeframe !== "1w" ? (
              <p className="workspace-note">{t("bt.intradayNote")}</p>
            ) : null}
            <p className="bt-actions">
              <button
                type="button"
                className="tb-button"
                disabled={busy}
                onClick={() => void saveAndRun(false)}
              >
                {t("bt.save")}
              </button>
              <button
                type="button"
                className="tb-button bt-primary"
                disabled={busy || !currency || chosen.length === 0}
                onClick={() => void saveAndRun(true)}
              >
                {t("bt.saveAndRun")}
              </button>
            </p>
          </div>

          <div className="bt-column bt-wide">
            <h3>{t("bt.runs")}</h3>
            {runs.length === 0 ? (
              <p className="workspace-note">{t("bt.noRuns")}</p>
            ) : (
              <table className="bt-table bt-runs" aria-label={t("bt.runs")}>
                <thead>
                  <tr>
                    <th />
                    <th>{t("bt.col.created")}</th>
                    <th>{t("bt.col.strategy")}</th>
                    <th>{t("bt.col.symbols")}</th>
                    <th>{t("bt.col.status")}</th>
                    <th>{t("bt.m.net")}</th>
                    <th>{t("bt.m.benchmark")}</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr
                      key={run.run_id}
                      className={
                        run.run_id === selectedRunId ? "selected" : undefined
                      }
                      onClick={() => setSelectedRunId(run.run_id)}
                    >
                      <td>
                        <button
                          type="button"
                          className="tb-button"
                          onClick={() => setSelectedRunId(run.run_id)}
                        >
                          {t("bt.view")}
                        </button>
                        {ACTIVE.has(run.status) ? (
                          <button
                            type="button"
                            className="tb-button"
                            onClick={(event) => {
                              event.stopPropagation();
                              void cancel(run.run_id);
                            }}
                          >
                            {t("bt.cancel")}
                          </button>
                        ) : null}
                      </td>
                      <td>
                        {run.created_at_utc.slice(0, 16).replace("T", " ")}
                      </td>
                      <td>
                        {strategyNames.get(run.strategy_id) ?? run.strategy_id}{" "}
                        v{run.strategy_version}
                      </td>
                      <td>
                        {run.config.instrument_ids.map(symbolOf).join(", ")} ·{" "}
                        {run.config.timeframe}
                      </td>
                      <td title={run.error ?? undefined}>{status(run)}</td>
                      <td>{percent(run.summary?.net_return)}</td>
                      <td>{percent(run.summary?.benchmark_return)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {selectedRun ? (
              <section aria-label={t("bt.resultsFor")}>
                <h3>
                  {t("bt.resultsFor")}:{" "}
                  {strategyNames.get(selectedRun.strategy_id) ??
                    selectedRun.strategy_id}{" "}
                  v{selectedRun.strategy_version} · {status(selectedRun)}
                </h3>
                {selectedRun.status === "failed" ? (
                  <p className="bt-problem" role="alert">
                    {selectedRun.error}
                  </p>
                ) : null}
                {ACTIVE.has(selectedRun.status) ? (
                  <progress
                    max={1}
                    value={selectedRun.progress}
                    aria-label={t("bt.progress")}
                  />
                ) : null}
                {results && results.runId === selectedRun.run_id ? (
                  <BacktestResultsView
                    run={selectedRun}
                    results={results.results}
                    agentApi={agentApi}
                    symbolOf={symbolOf}
                    real={real}
                    shown={shownRunId === selectedRun.run_id}
                    onToggleShown={() =>
                      onShowFills(
                        shownRunId === selectedRun.run_id
                          ? null
                          : {
                              runId: selectedRun.run_id,
                              fills: results.results.fills,
                            },
                      )
                    }
                  />
                ) : null}
              </section>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}

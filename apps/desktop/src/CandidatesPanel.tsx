import type {
  BacktestStrategy,
  ScreeningCandidates,
  ScreeningCheck,
} from "@siw/contracts/types";
import { Fragment, useEffect, useState } from "react";

import { useI18n } from "./i18n";
import type { InstrumentListing } from "./market-api";
import type { ChartPriceLevel } from "./PriceChart";
import type { ScreeningApi } from "./screening-api";

const STRATEGY_KEY = "siw.screening.strategy";

interface CandidatesPanelProps {
  api: ScreeningApi;
  /** Saved strategies; only rules strategies can be checked on daily bars. */
  strategies: () => Promise<BacktestStrategy[]>;
  onPick: (listing: InstrumentListing) => void;
  /** Draws a check's price levels on charts of that instrument; null clears them. */
  onShowLevels?: (overlay: ScreeningOverlay | null) => void;
  /** The levels drawn now, so the panel can offer to clear them. */
  overlay?: ScreeningOverlay | null;
}

/** Price levels of one stored check, drawn on charts of its instrument (ADR 0052 §7). */
export interface ScreeningOverlay {
  instrumentId: string;
  symbol: string;
  checkId: number;
  levels: ChartPriceLevel[];
}

/** Checks other than entry, run one candidate at a time. */
type OtherKind =
  "news" | "dilution" | "off_exchange" | "off_exchange_structure" | "momentum";

type Candidate = ScreeningCandidates["candidates"][number];

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function remembered(): string {
  try {
    return localStorage.getItem(STRATEGY_KEY) ?? "";
  } catch {
    return "";
  }
}

function remember(strategyId: string): void {
  try {
    localStorage.setItem(STRATEGY_KEY, strategyId);
  } catch {
    // A blocked store only means the choice is not kept.
  }
}

const OTHER_KINDS: Exclude<OtherKind, "momentum">[] = [
  "news",
  "dilution",
  "off_exchange",
  "off_exchange_structure",
];

/** Colors of drawn levels: one per check, readable on light and dark charts. */
const LEVEL_COLORS = {
  dilution: "#d97706",
  off_exchange: "#2563eb",
  off_exchange_structure: "#7c3aed",
} as const;

type ShowLevels = (check: ScreeningCheck, levels: ChartPriceLevel[]) => void;

/** A decimal string as a chart price; null for an unknown or unusable value. */
function priceOf(value: string | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

const STATUSES = [
  "passed",
  "failed",
  "material",
  "no_material",
  "ok",
  "incomplete",
  "unsupported",
  "no_data",
  "error",
  "pending",
] as const;

function statusText(
  t: ReturnType<typeof useI18n>["t"],
  status: string,
): string {
  const known = STATUSES.find((item) => item === status);
  return known ? t(`candidates.status.${known}`) : status;
}

/** The record's opening lists: SIW saw the symbol there, not its entry. */
const LIST_SEEN = ["first_list", "list_after_gap", "list_on_renewal"];

/** Signed percent with two decimals, as the API gave it. */
function percentText(value: string | null | undefined): string {
  if (value === null || value === undefined) return "?";
  return value.startsWith("-") ? `${value}%` : `+${value}%`;
}

/** A dollar amount in short form: $950, $12.3K, $4.5M. */
function dollarsText(value: string | null | undefined): string {
  const number = value === null || value === undefined ? NaN : Number(value);
  if (!Number.isFinite(number)) return "?";
  if (number >= 1e9) return `$${(number / 1e9).toFixed(1)}B`;
  if (number >= 1e6) return `$${(number / 1e6).toFixed(1)}M`;
  if (number >= 1e3) return `$${(number / 1e3).toFixed(1)}K`;
  return `$${number.toFixed(0)}`;
}

/** HH:MM in New York, where the condition and the session live. */
function newYorkTime(utc: string): string {
  return new Date(utc).toLocaleTimeString("en-GB", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Today's screening candidates (ADR 0052 S1): what the realtime condition record listed
 * or added, plus tickers the user adds, with the entry check of a saved rules strategy on
 * the last closed daily bar. Picking a candidate opens its chart; nothing here orders.
 */
export function CandidatesPanel({
  api,
  strategies,
  onPick,
  onShowLevels,
  overlay = null,
}: CandidatesPanelProps) {
  const { t } = useI18n();
  const [document, setDocument] = useState<ScreeningCandidates | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const [choices, setChoices] = useState<BacktestStrategy[]>([]);
  const [strategyId, setStrategyId] = useState(remembered);
  const [running, setRunning] = useState<string[]>([]);
  const [runError, setRunError] = useState<string | null>(null);
  const [adding, setAdding] = useState("");
  const [detail, setDetail] = useState<ScreeningCheck | null>(null);
  /** Closed sessions check D reads (ADR 0052 §5); the cache holds twenty. */
  const [offSessions, setOffSessions] = useState(20);
  // The noon cut (ADR 0055): the record up to 12:00 New York time of the session.
  const [asOf, setAsOf] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setLoadError(null);
    api
      .candidates(undefined, asOf ?? undefined)
      .then((found) => current && setDocument(found))
      .catch((reason: unknown) => current && setLoadError(reason));
    return () => {
      current = false;
    };
  }, [api, reload, asOf]);

  useEffect(() => {
    let current = true;
    strategies()
      .then((found) => {
        if (current)
          setChoices(found.filter((item) => item.strategy_type === "rules"));
      })
      .catch(() => current && setChoices([]));
    return () => {
      current = false;
    };
  }, [strategies]);

  const chosen = choices.find((item) => item.strategy_id === strategyId);
  const check = async (candidates: Candidate[]) => {
    if (!chosen) return;
    setRunError(null);
    for (const candidate of candidates) {
      setRunning((list) => [...list, candidate.symbol]);
      try {
        await api.runEntryCheck(
          candidate.symbol,
          chosen.strategy_id,
          document?.session_date,
        );
      } catch (reason) {
        setRunError(
          t("candidates.checkFailed", {
            symbol: candidate.symbol,
            error: errorText(reason),
          }),
        );
      } finally {
        setRunning((list) => list.filter((item) => item !== candidate.symbol));
      }
    }
    setReload((value) => value + 1);
  };
  const add = () => {
    const symbol = adding.trim().toUpperCase();
    if (!symbol) return;
    api
      .addCandidate(symbol, document?.session_date)
      .then((found) => {
        setDocument(found);
        setAdding("");
      })
      .catch((reason: unknown) =>
        setRunError(t("candidates.addFailed", { error: errorText(reason) })),
      );
  };
  const runOne = (kind: OtherKind, symbol: string, llm: boolean) => {
    const date = document?.session_date;
    if (kind === "news") return api.runNewsCheck(symbol, date);
    if (kind === "momentum") return api.runMomentumCheck(symbol, date);
    if (kind === "off_exchange")
      return api.runOffExchangeCheck(symbol, date, offSessions);
    if (kind === "off_exchange_structure")
      return api.runStructureCheck(symbol, date);
    return api.runDilutionCheck(symbol, llm, date);
  };
  const run = async (
    kinds: OtherKind[],
    candidates: Candidate[],
    llm = false,
  ) => {
    setRunError(null);
    for (const candidate of candidates) {
      for (const kind of kinds) {
        const key = `${kind}:${candidate.symbol}`;
        setRunning((list) => [...list, key]);
        try {
          const found = await runOne(kind, candidate.symbol, llm);
          if (llm) setDetail(found);
        } catch (reason) {
          setRunError(
            t("candidates.checkFailed", {
              symbol: candidate.symbol,
              error: errorText(reason),
            }),
          );
        } finally {
          setRunning((list) => list.filter((item) => item !== key));
        }
      }
    }
    setReload((value) => value + 1);
  };
  const dilution = (candidates: Candidate[], llm: boolean) =>
    run(["dilution"], candidates, llm);
  /** A waiting check looked at again, by the route its kind has. */
  const lookAgain = (
    found: { check_id: number },
    kind: OtherKind,
    symbol: string,
  ) => {
    const again =
      kind === "news"
        ? api.refreshNewsCheck(found.check_id)
        : kind === "off_exchange_structure"
          ? // Posting again shows the progress, then the result once sessions are cached.
            api.runStructureCheck(symbol, document?.session_date)
          : kind === "off_exchange"
            ? api.runOffExchangeCheck(
                symbol,
                document?.session_date,
                offSessions,
              )
            : api.refreshCheck(found.check_id);
    again
      .then((next) => {
        setDetail(next);
        setReload((value) => value + 1);
      })
      .catch((reason: unknown) => setRunError(errorText(reason)));
  };
  const refresh = (checkId: number) => {
    if (!detail) return;
    lookAgain({ check_id: checkId }, detail.check as OtherKind, detail.symbol);
  };
  const showDetail = (checkId: number) => {
    api
      .check(checkId)
      .then(setDetail)
      .catch((reason: unknown) => setRunError(errorText(reason)));
  };

  const show = onShowLevels
    ? (check: ScreeningCheck, levels: ChartPriceLevel[]) => {
        onShowLevels({
          instrumentId: check.instrument_id,
          symbol: check.symbol,
          checkId: check.check_id,
          levels,
        });
        const candidate = document?.candidates.find(
          (item) => item.symbol === check.symbol,
        );
        // The levels are drawn on that ticker's chart, so show it.
        if (candidate?.listing) onPick(candidate.listing);
      }
    : undefined;

  const chartable = (document?.candidates ?? []).filter(
    (item) => item.listing !== null,
  );
  const coverage = document?.watch_coverage;
  // Only symbols from the condition record have an entry to look before.
  const fromCondition = (document?.candidates ?? []).filter(
    (item) => item.listing !== null && item.sources.includes("condition"),
  );
  const noon = document?.selection_noon_utc ?? null;
  const firstAfterNoon =
    noon === null
      ? -1
      : (document?.candidates ?? []).findIndex(
          (item) => item.first_seen_utc >= noon,
        );

  return (
    <section
      className="quote-panel research-panel"
      aria-label={t("candidates.title")}
    >
      <h2>{t("candidates.title")}</h2>
      {loadError ? (
        <p className="chart-error" role="alert">
          {t("candidates.loadFailed", { error: errorText(loadError) })}{" "}
          <button
            type="button"
            className="secondary"
            onClick={() => setReload(reload + 1)}
          >
            {t("screener.retry")}
          </button>
        </p>
      ) : null}
      {document ? (
        <p className="workspace-note" role="status">
          {document.recorded
            ? t("candidates.coverage", {
                date: document.session_date,
                watches: coverage?.watches ?? 0,
                interrupted: coverage?.interrupted ?? 0,
                state: coverage?.open_now
                  ? t("candidates.recording")
                  : t("candidates.notRecording"),
              })
            : t("candidates.noRecord", { date: document.session_date })}
        </p>
      ) : null}
      {coverage && coverage.gaps.length > 0 ? (
        <p className="chart-error" role="alert">
          {t("candidates.gaps", {
            list: coverage.gaps
              .map(
                (gap) =>
                  `${newYorkTime(gap.from_utc)}–${newYorkTime(gap.to_utc)} ${t(`candidates.gap.${gap.reason}`)}${gap.previous_end ? ` (${gap.previous_end})` : ""}`,
              )
              .join(", "),
          })}
        </p>
      ) : null}
      <div className="trade-fields">
        <label>
          {t("candidates.strategy")}
          <select
            value={strategyId}
            onChange={(event) => {
              setStrategyId(event.target.value);
              remember(event.target.value);
            }}
          >
            <option value="">{t("candidates.pickStrategy")}</option>
            {choices.map((item) => (
              <option key={item.strategy_id} value={item.strategy_id}>
                {item.name} v{item.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("candidates.offExchangeSessions")}
          <select
            value={offSessions}
            onChange={(event) => setOffSessions(Number(event.target.value))}
          >
            {[5, 10, 20].map((count) => (
              <option key={count} value={count}>
                {count}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={!chosen || running.length > 0 || chartable.length === 0}
          onClick={() => void check(chartable)}
        >
          {t("candidates.checkAll")}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={running.length > 0 || chartable.length === 0}
          onClick={() => void dilution(chartable, false)}
        >
          {t("candidates.dilutionAll")}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={running.length > 0 || chartable.length === 0}
          onClick={() =>
            void run(
              ["news", "off_exchange", "off_exchange_structure"],
              chartable,
            )
          }
        >
          {t("candidates.othersAll")}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={running.length > 0 || fromCondition.length === 0}
          onClick={() => void run(["momentum"], fromCondition)}
        >
          {t("candidates.momentumAll")}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => setReload(reload + 1)}
        >
          {t("candidates.refresh")}
        </button>
        <label>
          <input
            type="checkbox"
            checked={asOf !== null}
            disabled={!noon}
            onChange={(event) => setAsOf(event.target.checked ? noon : null)}
          />
          {t("candidates.noonCut")}
        </label>
        {overlay && onShowLevels ? (
          <button
            type="button"
            className="secondary"
            onClick={() => onShowLevels(null)}
          >
            {t("candidates.clearLevels", { symbol: overlay.symbol })}
          </button>
        ) : null}
      </div>
      <form
        className="trade-fields"
        onSubmit={(event) => {
          event.preventDefault();
          add();
        }}
      >
        <label>
          {t("candidates.add")}
          <input
            value={adding}
            maxLength={10}
            onChange={(event) => setAdding(event.target.value)}
          />
        </label>
        <button type="submit" className="secondary">
          {t("candidates.addButton")}
        </button>
      </form>
      {runError ? (
        <p className="chart-error" role="alert">
          {runError}
        </p>
      ) : null}
      {document && document.candidates.length > 0 ? (
        <div className="research-scroll">
          <table className="quote-trades research-table">
            <thead>
              <tr>
                <th>{t("screener.symbol")}</th>
                <th>{t("candidates.firstSeen")}</th>
                <th>{t("candidates.inList")}</th>
                <th>{t("candidates.flicker")}</th>
                <th>{t("candidates.column.momentum")}</th>
                <th>{t("candidates.entry")}</th>
                {OTHER_KINDS.map((kind) => (
                  <th key={kind}>{t(`candidates.column.${kind}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {document.candidates.map((item, index) => {
                const listing = item.listing;
                const entry = item.checks.entry;
                const momentum = item.checks.momentum;
                const unknown = LIST_SEEN.includes(item.first_seen_how);
                const row = (
                  <tr key={item.symbol}>
                    <td>
                      {listing ? (
                        <button
                          type="button"
                          className="watch-symbol"
                          aria-label={t("watch.show", { symbol: item.symbol })}
                          onClick={() => onPick(listing)}
                        >
                          {item.symbol}
                        </button>
                      ) : (
                        <span title={t("screener.unknown")}>{item.symbol}</span>
                      )}
                    </td>
                    <td
                      title={
                        unknown && item.entered_after_utc
                          ? t("candidates.entryUnknown", {
                              from: newYorkTime(item.entered_after_utc),
                              to: newYorkTime(item.first_seen_utc),
                            })
                          : item.first_seen_utc
                      }
                    >
                      {unknown && item.entered_after_utc
                        ? `${newYorkTime(item.entered_after_utc)}–${newYorkTime(item.first_seen_utc)}?`
                        : newYorkTime(item.first_seen_utc)}
                    </td>
                    <td>
                      {item.in_list === null
                        ? t("candidates.manual")
                        : item.in_list
                          ? t("candidates.yes")
                          : t("candidates.gone")}
                    </td>
                    <td>
                      {item.enter_count === null ||
                      item.seconds_in_list === null
                        ? "—"
                        : t("candidates.flickerValue", {
                            count: item.enter_count,
                            minutes: Math.round(item.seconds_in_list / 60),
                          })}
                    </td>
                    <td>
                      {running.includes(`momentum:${item.symbol}`) ? (
                        t("screener.running")
                      ) : momentum ? (
                        <button
                          type="button"
                          className="secondary"
                          aria-label={`${item.symbol} ${t("candidates.column.momentum")}`}
                          onClick={() => showDetail(momentum.check_id)}
                        >
                          {momentum.summary &&
                          momentum.summary.change_percent !== null
                            ? `${percentText(momentum.summary.change_percent)} · ${dollarsText(momentum.summary.dollar_volume_estimate)}`
                            : statusText(t, momentum.status)}
                        </button>
                      ) : listing && item.sources.includes("condition") ? (
                        <button
                          type="button"
                          className="secondary"
                          aria-label={`${item.symbol} ${t("candidates.column.momentum")}: ${t("candidates.check")}`}
                          onClick={() => void run(["momentum"], [item])}
                        >
                          {t("candidates.check")}
                        </button>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {running.includes(item.symbol) ? (
                        t("screener.running")
                      ) : entry ? (
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => showDetail(entry.check_id)}
                        >
                          {statusText(t, entry.status)}
                        </button>
                      ) : listing && chosen ? (
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => void check([item])}
                        >
                          {t("candidates.check")}
                        </button>
                      ) : (
                        "—"
                      )}
                    </td>
                    {OTHER_KINDS.map((kind) => {
                      const found = item.checks[kind];
                      return (
                        <td key={kind}>
                          {running.includes(`${kind}:${item.symbol}`) ? (
                            t("screener.running")
                          ) : found ? (
                            <button
                              type="button"
                              className="secondary"
                              aria-label={`${item.symbol} ${t(`candidates.column.${kind}`)}: ${statusText(t, found.status)}`}
                              onClick={() =>
                                found.status === "pending"
                                  ? lookAgain(found, kind, item.symbol)
                                  : showDetail(found.check_id)
                              }
                            >
                              {statusText(t, found.status)}
                            </button>
                          ) : listing ? (
                            <button
                              type="button"
                              className="secondary"
                              aria-label={`${item.symbol} ${t(`candidates.column.${kind}`)}: ${t("candidates.check")}`}
                              onClick={() => void run([kind], [item])}
                            >
                              {t("candidates.check")}
                            </button>
                          ) : (
                            "—"
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
                return index === firstAfterNoon && asOf === null ? (
                  <Fragment key={item.symbol}>
                    <tr>
                      <th colSpan={6 + OTHER_KINDS.length}>
                        {t("candidates.afterNoon")}
                      </th>
                    </tr>
                    {row}
                  </Fragment>
                ) : (
                  row
                );
              })}
            </tbody>
          </table>
        </div>
      ) : document ? (
        <p className="workspace-note">{t("candidates.empty")}</p>
      ) : null}
      {detail && detail.check === "entry" ? (
        <EntryDetail check={detail} onClose={() => setDetail(null)} />
      ) : null}
      {detail && detail.check === "dilution" ? (
        <DilutionDetail
          check={detail}
          onClose={() => setDetail(null)}
          onShow={show}
          onRefresh={() => refresh(detail.check_id)}
          onLlm={() => {
            const candidate = document?.candidates.find(
              (item) => item.symbol === detail.symbol,
            );
            if (candidate) void dilution([candidate], true);
          }}
        />
      ) : null}
      {detail && detail.check === "momentum" ? (
        <MomentumDetail check={detail} onClose={() => setDetail(null)} />
      ) : null}
      {detail && detail.check === "news" ? (
        <NewsDetail
          check={detail}
          onClose={() => setDetail(null)}
          onRefresh={() => refresh(detail.check_id)}
        />
      ) : null}
      {detail && detail.check === "off_exchange" ? (
        <OffExchangeDetail
          check={detail}
          onClose={() => setDetail(null)}
          onShow={show}
        />
      ) : null}
      {detail && detail.check === "off_exchange_structure" ? (
        <StructureDetail
          check={detail}
          onClose={() => setDetail(null)}
          onRefresh={() => refresh(detail.check_id)}
          onShow={show}
        />
      ) : null}
      <p className="workspace-note">{t("candidates.note")}</p>
    </section>
  );
}

interface EntryCondition {
  path: string;
  op: string;
  left_value: string | null;
  right_value: string | null;
  holds: boolean;
}

function EntryDetail({
  check,
  onClose,
}: {
  check: ScreeningCheck;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const result = check.result as {
    reason?: string;
    bar?: { session_date: string; close: string };
    conditions?: EntryCondition[];
  };
  return (
    <div
      className="research-scroll"
      role="region"
      aria-label={t("candidates.detail")}
    >
      <p className="workspace-note">
        {t("candidates.detailMeta", {
          symbol: check.symbol,
          strategy: check.strategy
            ? `${check.strategy.name} v${check.strategy.version}`
            : "—",
          date: result.bar?.session_date ?? "—",
          close: result.bar?.close ?? "—",
        })}
      </p>
      {result.reason ? <p className="chart-error">{result.reason}</p> : null}
      {result.conditions ? (
        <table className="quote-trades research-table">
          <thead>
            <tr>
              <th>{t("candidates.rule")}</th>
              <th>{t("candidates.leftValue")}</th>
              <th>{t("candidates.op")}</th>
              <th>{t("candidates.rightValue")}</th>
              <th>{t("candidates.holds")}</th>
            </tr>
          </thead>
          <tbody>
            {result.conditions.map((item) => (
              <tr key={item.path}>
                <td>{item.path}</td>
                <td>{item.left_value ?? "—"}</td>
                <td>{item.op}</td>
                <td>{item.right_value ?? "—"}</td>
                <td>{item.holds ? "✓" : "✗"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {check.warnings.map((warning) => (
        <p key={warning} className="workspace-note">
          {warning}
        </p>
      ))}
      <button type="button" className="secondary" onClick={onClose}>
        {t("candidates.close")}
      </button>
    </div>
  );
}

interface MomentumResult {
  reason?: string;
  first_seen_utc?: string;
  entry_time_known?: boolean;
  bars_until_utc?: string;
  price?: { value: string; bar_end_utc: string; session: string } | null;
  previous_close?: { value: string; session_date: string } | null;
  change_percent?: string | null;
  volume?: string | null;
  dollar_volume_estimate?: string | null;
  missing_minutes?: Record<string, number>;
  missing?: string[];
}

/** The momentum check (ADR 0055): the market before the symbol entered the list. */
function MomentumDetail({
  check,
  onClose,
}: {
  check: ScreeningCheck;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const result = check.result as MomentumResult;
  const bars = (check.sources as { bars?: { source_feed_scope?: string } })
    .bars;
  const missing = Object.entries(result.missing_minutes ?? {});
  return (
    <div
      className="research-scroll"
      role="region"
      aria-label={t("candidates.momentumDetail")}
    >
      <p className="workspace-note">
        {t("candidates.momentumMeta", {
          symbol: check.symbol,
          time: result.first_seen_utc
            ? newYorkTime(result.first_seen_utc)
            : "—",
          until: result.bars_until_utc
            ? newYorkTime(result.bars_until_utc)
            : "—",
          source: bars?.source_feed_scope ?? "—",
        })}
      </p>
      {result.entry_time_known === false ? (
        <p className="chart-error">{t("candidates.momentumUnknown")}</p>
      ) : null}
      {result.reason ? <p className="chart-error">{result.reason}</p> : null}
      <table className="quote-trades research-table">
        <tbody>
          <tr>
            <th>{t("candidates.lastClose")}</th>
            <td>
              {result.price
                ? `${result.price.value} (${newYorkTime(result.price.bar_end_utc)}, ${result.price.session})`
                : "?"}
            </td>
          </tr>
          <tr>
            <th>{t("candidates.previousClose")}</th>
            <td>
              {result.previous_close
                ? `${result.previous_close.value} (${result.previous_close.session_date})`
                : "?"}
            </td>
          </tr>
          <tr>
            <th>{t("candidates.change")}</th>
            <td>{percentText(result.change_percent)}</td>
          </tr>
          <tr>
            <th>{t("candidates.volume")}</th>
            <td>{result.volume ?? "?"}</td>
          </tr>
          <tr>
            <th>{t("candidates.dollarVolume")}</th>
            <td>{dollarsText(result.dollar_volume_estimate)}</td>
          </tr>
          <tr>
            <th>{t("candidates.missingMinutes")}</th>
            <td>
              {missing.length > 0
                ? missing
                    .map(([reason, count]) => `${reason} ${count}`)
                    .join(", ")
                : "0"}
            </td>
          </tr>
        </tbody>
      </table>
      {(result.missing ?? []).map((line) => (
        <p key={line} className="workspace-note">
          {line}
        </p>
      ))}
      {check.warnings.map((warning) => (
        <p key={warning} className="workspace-note">
          {warning}
        </p>
      ))}
      <button type="button" className="secondary" onClick={onClose}>
        {t("candidates.close")}
      </button>
    </div>
  );
}

interface SupplyValue {
  amount: string | null;
  unit: string | null;
  status: string | null;
  fact: boolean;
}

interface SupplySecurity {
  id: string;
  kind: string;
  description: string;
  price: SupplyValue | null;
  price_range: (SupplyValue | null)[] | null;
  floor: SupplyValue | null;
  outstanding: SupplyValue | null;
  conditions: { type: string; threshold: SupplyValue | null }[];
  after_base_point: boolean;
  approval_unknown: boolean;
}

/** An amount as the analyzer gave it: unknown stays "?", a non-fact keeps its status. */
function supplyText(value: SupplyValue | null | undefined): string {
  if (!value || value.amount === null) return "?";
  return value.fact ? value.amount : `${value.amount} (${value.status ?? "?"})`;
}

/** Contract prices, floors and condition thresholds of a dilution result as chart lines. */
function supplyLevels(securities: SupplySecurity[]): ChartPriceLevel[] {
  const levels: ChartPriceLevel[] = [];
  const add = (value: SupplyValue | null | undefined, title: string) => {
    const price = priceOf(value?.amount);
    if (price === null || value?.unit !== "usd_per_share") return;
    levels.push({
      price,
      title,
      color: LEVEL_COLORS.dilution,
      // A value the analyzer did not confirm is drawn dotted.
      style: value.fact ? "dashed" : "dotted",
    });
  };
  for (const item of securities) {
    add(item.price, item.id);
    for (const bound of item.price_range ?? []) add(bound, item.id);
    add(item.floor, `${item.id} ≥`);
    for (const condition of item.conditions) add(condition.threshold, item.id);
  }
  return levels;
}

function DilutionDetail({
  check,
  onClose,
  onRefresh,
  onLlm,
  onShow,
}: {
  check: ScreeningCheck;
  onClose: () => void;
  onRefresh: () => void;
  onLlm: () => void;
  onShow?: ShowLevels;
}) {
  const { t } = useI18n();
  const result = check.result as {
    llm?: boolean;
    reason?: string;
    securities?: SupplySecurity[];
    capacity?: { security_id: string; nominal: SupplyValue | null }[];
    incomplete_reasons?: string[];
    issuer_warnings?: string[];
    shelves_notes?: string[];
    resale_notes?: string[];
  };
  return (
    <div
      className="research-scroll"
      role="region"
      aria-label={t("candidates.dilutionDetail")}
    >
      <p className="workspace-note">
        {t("candidates.dilutionMeta", {
          symbol: check.symbol,
          mode: result.llm ? "LLM" : t("candidates.rulesOnly"),
          status: statusText(t, check.status),
        })}
      </p>
      {result.reason ? <p className="chart-error">{result.reason}</p> : null}
      {result.incomplete_reasons && result.incomplete_reasons.length > 0 ? (
        <p className="workspace-note" role="status">
          {t("candidates.dilutionIncomplete", {
            reasons: result.incomplete_reasons.join(", "),
          })}
        </p>
      ) : null}
      {result.securities &&
      result.securities.length === 0 &&
      check.status !== "pending" ? (
        <p className="workspace-note" role="status">
          {check.status === "incomplete" || !result.llm
            ? t("candidates.noSecuritiesUnknown")
            : t("candidates.noSecuritiesFound")}
        </p>
      ) : null}
      {result.securities && result.securities.length > 0 ? (
        <table className="quote-trades research-table">
          <thead>
            <tr>
              <th>{t("candidates.security")}</th>
              <th>{t("candidates.price")}</th>
              <th>{t("candidates.floorOrRange")}</th>
              <th>{t("candidates.outstanding")}</th>
            </tr>
          </thead>
          <tbody>
            {result.securities.map((item) => (
              <tr key={item.id}>
                <td title={item.kind}>
                  {item.description}
                  {item.approval_unknown
                    ? ` · ${t("candidates.approvalUnknown")}`
                    : ""}
                </td>
                <td>{supplyText(item.price)}</td>
                <td>
                  {item.price_range
                    ? item.price_range.map(supplyText).join("–")
                    : item.floor
                      ? `≥ ${supplyText(item.floor)}`
                      : "—"}
                </td>
                <td>{supplyText(item.outstanding)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {(result.capacity ?? []).map((item) => (
        <p key={item.security_id} className="workspace-note">
          {t("candidates.capacity", {
            id: item.security_id,
            amount: supplyText(item.nominal),
          })}
        </p>
      ))}
      {[
        ...(result.issuer_warnings ?? []),
        ...(result.shelves_notes ?? []),
        ...(result.resale_notes ?? []),
        ...check.warnings,
      ].map((note) => (
        <p key={note} className="workspace-note">
          {note}
        </p>
      ))}
      <div className="trade-fields">
        {check.status === "pending" ? (
          <button type="button" className="secondary" onClick={onRefresh}>
            {t("candidates.lookAgain")}
          </button>
        ) : null}
        {result.llm ? null : (
          <button type="button" className="secondary" onClick={onLlm}>
            {t("candidates.withLlm")}
          </button>
        )}
        <ShowButton
          check={check}
          levels={supplyLevels(result.securities ?? [])}
          onShow={onShow}
        />
        <button type="button" className="secondary" onClick={onClose}>
          {t("candidates.close")}
        </button>
      </div>
    </div>
  );
}

/** Draws a check's levels on the chart; absent when there is nothing to draw. */
function ShowButton({
  check,
  levels,
  onShow,
}: {
  check: ScreeningCheck;
  levels: ChartPriceLevel[];
  onShow?: ShowLevels;
}) {
  const { t } = useI18n();
  if (!onShow || levels.length === 0) return null;
  return (
    <button
      type="button"
      className="secondary"
      onClick={() => onShow(check, levels)}
    >
      {t("candidates.showLevels", { count: levels.length })}
    </button>
  );
}

function Notes({ notes }: { notes: string[] }) {
  return (
    <>
      {[...new Set(notes)].map((note) => (
        <p key={note} className="workspace-note">
          {note}
        </p>
      ))}
    </>
  );
}

interface NewsResult {
  reason?: string | null;
  lookback_hours?: number;
  article_count?: number;
  categories?: Record<string, number>;
  out_of_scope?: { dilution?: { articles?: number } };
  feeds?: {
    source_id: string;
    state: string;
    articles: number;
    truncated: boolean;
  }[];
  latest_categorized?: {
    headline?: string;
    published_at_utc?: string;
    categories: string[];
  } | null;
  /** The service's LLM reading (news-analysis ADR 0002): inference, not fact. */
  nir?: {
    label: "INFERENCE";
    state: string | null;
    reason: string | null;
    rules_only: boolean;
    model: string | null;
    items: NirItem[];
  };
}

interface NirItem {
  nir_record_id: string | null;
  headline?: string | null;
  published_at_utc?: string | null;
  event_type?: string;
  counterparty?: string;
  legally_binding?: string;
  committed_amount?: string;
  potential_amount?: string;
  economic_substance?: string;
  promotion_risk?: string;
  confidence?: string;
  summary_ko?: string;
  verification_failures?: number;
  rule_conflicts?: string[];
}

const NEWS_CATEGORIES = [
  "binding_contract",
  "non_binding_intent",
  "partnership",
  "earnings",
  "clinical_fda",
  "merger_acquisition",
  "reverse_split",
  "listing_notice",
] as const;

/** Check B's LLM digest, always marked as inference; or a "rules only" note. */
export function NirDigest({ result }: { result: NewsResult }) {
  const { t } = useI18n();
  const nir = result.nir;
  const tagged = Object.values(result.categories ?? {}).some((n) => n > 0);
  if (nir && nir.items.length > 0) {
    return (
      <section aria-label={t("candidates.nirTitle")}>
        <p className="workspace-note" role="note">
          {t("candidates.nirNotice")}
        </p>
        <ul>
          {nir.items.map((item, index) => (
            <li key={item.nir_record_id ?? index}>
              {t("candidates.nirItem", {
                event: item.event_type ?? "UNKNOWN",
                binding: item.legally_binding ?? "UNKNOWN",
                amount: item.committed_amount ?? "UNKNOWN",
                potential: item.potential_amount ?? "UNKNOWN",
                substance: item.economic_substance ?? "UNKNOWN",
                promotion: item.promotion_risk ?? "UNKNOWN",
                confidence: item.confidence ?? "UNKNOWN",
              })}
              {item.summary_ko ? <> — {item.summary_ko}</> : null}
              {item.headline ? (
                <span className="workspace-note"> ({item.headline})</span>
              ) : null}
              {(item.verification_failures ?? 0) > 0 ||
              (item.rule_conflicts ?? []).length > 0 ? (
                <span className="chart-error">
                  {" "}
                  {t("candidates.nirChecks", {
                    failures: item.verification_failures ?? 0,
                    conflicts: (item.rule_conflicts ?? []).length,
                  })}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    );
  }
  if (!tagged || nir?.state === "not_needed") return null;
  return (
    <p className="workspace-note" role="note">
      {nir?.reason
        ? t("candidates.nirRulesOnlyReason", { reason: nir.reason })
        : t("candidates.nirRulesOnly")}
    </p>
  );
}

function categoryText(
  t: ReturnType<typeof useI18n>["t"],
  category: string,
): string {
  const known = NEWS_CATEGORIES.find((item) => item === category);
  return known ? t(`candidates.news.${known}`) : category;
}

/** Check B: catalyst categories the news-analysis service found before the cutoff. */
function NewsDetail({
  check,
  onClose,
  onRefresh,
}: {
  check: ScreeningCheck;
  onClose: () => void;
  onRefresh: () => void;
}) {
  const { t } = useI18n();
  const result = check.result as NewsResult;
  const categories = Object.entries(result.categories ?? {}).filter(
    ([, count]) => count > 0,
  );
  const dilution = result.out_of_scope?.dilution?.articles ?? 0;
  return (
    <div
      className="research-scroll"
      role="region"
      aria-label={t("candidates.newsDetail")}
    >
      <p className="workspace-note">
        {t("candidates.newsMeta", {
          symbol: check.symbol,
          hours: result.lookback_hours ?? "—",
          articles: result.article_count ?? 0,
          status: statusText(t, check.status),
        })}
      </p>
      {result.reason ? <p className="chart-error">{result.reason}</p> : null}
      {categories.length > 0 ? (
        <ul>
          {categories.map(([category, count]) => (
            <li key={category}>
              {categoryText(t, category)}: {count}
            </li>
          ))}
        </ul>
      ) : null}
      {result.latest_categorized ? (
        <p className="workspace-note">
          {t("candidates.newsLatest", {
            time: result.latest_categorized.published_at_utc
              ? newYorkTime(result.latest_categorized.published_at_utc)
              : "—",
            headline: result.latest_categorized.headline ?? "—",
            categories: result.latest_categorized.categories
              .map((item) => categoryText(t, item))
              .join(", "),
          })}
        </p>
      ) : null}
      <NirDigest result={result} />
      {dilution > 0 ? (
        <p className="workspace-note" role="status">
          {t("candidates.newsDilution", { count: dilution })}
        </p>
      ) : null}
      {(result.feeds ?? []).length > 0 ? (
        <p className="workspace-note">
          {(result.feeds ?? [])
            .map(
              (feed) =>
                `${feed.source_id}: ${feed.state} (${feed.articles}${feed.truncated ? "+" : ""})`,
            )
            .join(" · ")}
        </p>
      ) : null}
      <Notes notes={check.warnings} />
      <div className="trade-fields">
        {check.status === "pending" ? (
          <button type="button" className="secondary" onClick={onRefresh}>
            {t("candidates.lookAgain")}
          </button>
        ) : null}
        <button type="button" className="secondary" onClick={onClose}>
          {t("candidates.close")}
        </button>
      </div>
    </div>
  );
}

interface OffExchangeLevel {
  price_low: string;
  price_high: string;
  volume: string;
  share_pct: string;
  side: string;
}

interface OffExchangeResult {
  reason?: string;
  sessions_requested?: number;
  sessions?: {
    session_date: string;
    source?: "live" | "cache";
    partial: boolean;
    truncated: boolean;
  }[];
  progress?: {
    total_sessions: number;
    cached_sessions: number;
    reading_sessions: number;
  };
  reference_price?: string | null;
  off_exchange_share_pct?: string | null;
  levels?: OffExchangeLevel[];
  missing?: string[];
  notes?: string[];
}

function sideText(t: ReturnType<typeof useI18n>["t"], side: string): string {
  return side === "above" || side === "below" || side === "at"
    ? t(`candidates.side.${side}`)
    : side;
}

/** Check D: off-exchange (TRF-reported) volume by price over recent sessions. */
function OffExchangeDetail({
  check,
  onClose,
  onShow,
}: {
  check: ScreeningCheck;
  onClose: () => void;
  onShow?: ShowLevels;
}) {
  const { t } = useI18n();
  const result = check.result as OffExchangeResult;
  const sessions = result.sessions ?? [];
  const cut = sessions.filter((item) => item.truncated).length;
  const live = sessions.some((item) => item.source === "live");
  const closed = sessions.filter((item) => item.source !== "live").length;
  const progress = result.progress;
  const levels: ChartPriceLevel[] = [];
  for (const level of result.levels ?? []) {
    const low = priceOf(level.price_low);
    const high = priceOf(level.price_high);
    const title = `${level.share_pct}%`;
    if (low !== null)
      levels.push({ price: low, title, color: LEVEL_COLORS.off_exchange });
    if (high !== null && high !== low)
      levels.push({ price: high, title: "", color: LEVEL_COLORS.off_exchange });
  }
  return (
    <div
      className="research-scroll"
      role="region"
      aria-label={t("candidates.offExchangeDetail")}
    >
      <p className="workspace-note">
        {t("candidates.offExchangeMeta", {
          symbol: check.symbol,
          sessions: closed,
          requested: result.sessions_requested ?? "—",
          today: live ? t("candidates.offExchangeToday") : "",
          share: result.off_exchange_share_pct ?? "?",
          reference: result.reference_price ?? "?",
          status: statusText(t, check.status),
        })}
      </p>
      {progress && check.status === "pending" ? (
        <p className="workspace-note" role="status">
          {t("candidates.structureProgress", {
            cached: progress.cached_sessions,
            total: progress.total_sessions,
          })}
        </p>
      ) : null}
      {cut > 0 ? (
        <p className="workspace-note" role="status">
          {t("candidates.offExchangeCut", { count: cut })}
        </p>
      ) : null}
      {result.reason ? <p className="chart-error">{result.reason}</p> : null}
      {(result.levels ?? []).length > 0 ? (
        <table className="quote-trades research-table">
          <thead>
            <tr>
              <th>{t("candidates.priceBand")}</th>
              <th>{t("candidates.share")}</th>
              <th>{t("candidates.side")}</th>
            </tr>
          </thead>
          <tbody>
            {(result.levels ?? []).map((level) => (
              <tr key={level.price_low}>
                <td>
                  {level.price_low}–{level.price_high}
                </td>
                <td>{level.share_pct}%</td>
                <td>{sideText(t, level.side)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <Notes notes={[...(result.notes ?? []), ...check.warnings]} />
      <div className="trade-fields">
        <ShowButton check={check} levels={levels} onShow={onShow} />
        <button type="button" className="secondary" onClick={onClose}>
          {t("candidates.close")}
        </button>
      </div>
    </div>
  );
}

interface StructureZone {
  zone_id: string;
  low: string;
  high: string;
  side: string;
  period_share?: string | null;
}

interface StructureResult {
  reason?: string;
  progress?: {
    total_sessions: number;
    cached_sessions: number;
    reading_sessions: number;
  };
  forecast_status?: "ISSUED" | "NOT_ISSUED";
  stage?: string;
  deployment_stage?: string;
  reference_price?: { value: string | null };
  zones?: StructureZone[];
  gaps?: Record<
    string,
    {
      gap_is_open_ended?: boolean;
      gap_low?: string | null;
      gap_high?: string | null;
    } | null
  >;
  eligibility?: {
    status: string;
    hard_failures: { code: string; text: string }[];
    soft_failures: { code: string; text: string }[];
  };
  notes?: string[];
  limitations?: string[];
}

/** The stage shown only for an issued forecast; otherwise "no stage" or "research". */
function stageText(
  t: ReturnType<typeof useI18n>["t"],
  result: StructureResult,
): string {
  if (result.forecast_status !== "ISSUED" || !result.stage)
    return result.deployment_stage === "RESEARCH_ONLY"
      ? t("candidates.stage.research")
      : t("candidates.stage.none");
  return result.stage === "UNKNOWN" ? t("candidates.stage.none") : result.stage;
}

/** Off-exchange price structure (ADR 0053): zones and gaps; a stage only when issued. */
function StructureDetail({
  check,
  onClose,
  onRefresh,
  onShow,
}: {
  check: ScreeningCheck;
  onClose: () => void;
  onRefresh: () => void;
  onShow?: ShowLevels;
}) {
  const { t } = useI18n();
  const result = check.result as StructureResult;
  const zones = result.zones ?? [];
  const levels: ChartPriceLevel[] = [];
  for (const zone of zones) {
    const low = priceOf(zone.low);
    const high = priceOf(zone.high);
    if (low !== null)
      levels.push({
        price: low,
        title: zone.zone_id,
        color: LEVEL_COLORS.off_exchange_structure,
      });
    if (high !== null && high !== low)
      levels.push({
        price: high,
        title: "",
        color: LEVEL_COLORS.off_exchange_structure,
      });
  }
  const gaps = Object.entries(result.gaps ?? {}).filter(
    (entry): entry is [string, NonNullable<(typeof entry)[1]>] =>
      entry[1] !== null,
  );
  const progress = result.progress;
  return (
    <div
      className="research-scroll"
      role="region"
      aria-label={t("candidates.structureDetail")}
    >
      <p className="workspace-note">
        {t("candidates.structureMeta", {
          symbol: check.symbol,
          stage: stageText(t, result),
          reference: result.reference_price?.value ?? "?",
          status: statusText(t, check.status),
        })}
      </p>
      {progress && check.status === "pending" ? (
        <p className="workspace-note" role="status">
          {t("candidates.structureProgress", {
            cached: progress.cached_sessions,
            total: progress.total_sessions,
          })}
        </p>
      ) : null}
      {result.reason ? <p className="chart-error">{result.reason}</p> : null}
      {zones.length > 0 ? (
        <table className="quote-trades research-table">
          <thead>
            <tr>
              <th>{t("candidates.zone")}</th>
              <th>{t("candidates.priceBand")}</th>
              <th>{t("candidates.side")}</th>
            </tr>
          </thead>
          <tbody>
            {zones.map((zone) => (
              <tr key={zone.zone_id}>
                <td>{zone.zone_id}</td>
                <td>
                  {zone.low}–{zone.high}
                </td>
                <td>{sideText(t, zone.side)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {gaps.map(([direction, gap]) => (
        <p key={direction} className="workspace-note">
          {t(direction === "up" ? "candidates.gapUp" : "candidates.gapDown", {
            band: gap.gap_is_open_ended
              ? t("candidates.gapOpen")
              : `${gap.gap_low ?? "?"}–${gap.gap_high ?? "?"}`,
          })}
        </p>
      ))}
      {result.eligibility && result.eligibility.hard_failures.length > 0 ? (
        <>
          <p className="workspace-note">{t("candidates.whyNoStage")}</p>
          <ul>
            {result.eligibility.hard_failures.map((item) => (
              <li key={item.code} title={item.code}>
                {item.text}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <Notes notes={[...(result.notes ?? []), ...check.warnings]} />
      <div className="trade-fields">
        {check.status === "pending" ? (
          <button type="button" className="secondary" onClick={onRefresh}>
            {t("candidates.lookAgain")}
          </button>
        ) : null}
        <ShowButton check={check} levels={levels} onShow={onShow} />
        <button type="button" className="secondary" onClick={onClose}>
          {t("candidates.close")}
        </button>
      </div>
    </div>
  );
}

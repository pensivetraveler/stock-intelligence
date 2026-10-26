import type { PriceBar, ReviewTradeListResponse } from "@siw/contracts/types";
import { useCallback, useEffect, useMemo, useState } from "react";

import { accountLabel } from "./account-label";
import { useI18n } from "./i18n";
import type { InstrumentListing, MarketApi } from "./market-api";
import { exchangeTime } from "./chart-data";
import { zoneOf } from "./market-zone";
import {
  executionRefs,
  manualGroupOf,
  type ReviewApi,
  type ReviewLedger,
  type ReviewRule,
  type ReviewTrade,
  type TradeFilter,
} from "./review-api";
import { ReviewSettingsPanel } from "./ReviewSettingsPanel";
import { TradeChart } from "./TradeChart";
import { TradeReportPanel } from "./TradeReportPanel";
import { joinBars, tradeChartRequests, tradeMarkers } from "./trade-review";

const PAGE = 300;
const RULES: ReviewRule[] = ["flat", "day", "fifo", "manual"];
const LEDGERS: ReviewLedger[] = ["broker", "imported", "manual"];

type Strategies = ReviewTradeListResponse["strategies"];

interface Filters {
  ledger: ReviewLedger;
  strategyId: string;
  status: "" | ReviewTrade["status"];
  reviewed: "" | "yes" | "no";
  tag: string;
  onlyActive: boolean;
}

const START: Filters = {
  ledger: "broker",
  strategyId: "",
  status: "",
  reviewed: "",
  tag: "",
  onlyActive: false,
};

/** "us:aapl:xnas:2026" → "AAPL" when the listing is not loaded. */
function symbolFromId(instrumentId: string): string {
  return (instrumentId.split(":")[1] ?? instrumentId).toUpperCase();
}

function firstRef(trade: ReviewTrade): string {
  const item = trade.executions[0];
  return item ? `${item.ledger}:${item.execution_id}` : trade.trade_id;
}

function tagsFrom(text: string): string[] {
  return [
    ...new Set(
      text
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    ),
  ];
}

/**
 * The trade review screen (ADR 0042, step 1): trades grouped from the journal, a chart of
 * the chosen one, and its strategy, rule, notes and tags. Nothing here calls Claude.
 */
export function TradeReviewScreen({
  api,
  marketApi,
  instruments,
  activeInstrumentId,
  onClose,
  today = new Date().toISOString().slice(0, 10),
}: {
  api: ReviewApi;
  marketApi: MarketApi;
  instruments: InstrumentListing[];
  activeInstrumentId: string;
  onClose: () => void;
  today?: string;
}) {
  const { t } = useI18n();
  const [filters, setFilters] = useState<Filters>(START);
  const [list, setList] = useState<ReviewTradeListResponse | null>(null);
  const [trades, setTrades] = useState<ReviewTrade[]>([]);
  const [selectedRef, setSelectedRef] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const message = useCallback(
    (error: unknown) =>
      error instanceof Error ? error.message : t("common.requestFailed"),
    [t],
  );

  const symbols = useMemo(
    () =>
      new Map(
        instruments.map((item) => [
          item.instrument.instrument_id,
          item.instrument.symbol,
        ]),
      ),
    [instruments],
  );
  const symbolOf = (id: string) => symbols.get(id) ?? symbolFromId(id);

  const query = useCallback(
    (offset: number): TradeFilter => ({
      ledger: filters.ledger,
      ...(filters.onlyActive ? { instrumentId: activeInstrumentId } : {}),
      ...(filters.strategyId ? { strategyId: filters.strategyId } : {}),
      ...(filters.status ? { status: filters.status } : {}),
      ...(filters.reviewed ? { reviewed: filters.reviewed === "yes" } : {}),
      ...(filters.tag ? { tag: filters.tag } : {}),
      limit: PAGE,
      offset,
    }),
    [filters, activeInstrumentId],
  );

  const load = useCallback(() => {
    setBusy(true);
    return api
      .trades(query(0))
      .then((response) => {
        setList(response);
        setTrades(response.trades);
        setChecked(new Set());
        setProblem(null);
      })
      .catch((error: unknown) => setProblem(message(error)))
      .finally(() => setBusy(false));
  }, [api, query, message]);

  useEffect(() => {
    void load();
  }, [load]);

  // Escape closes the screen even when focus is not inside it.
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const loadMore = () => {
    setBusy(true);
    api
      .trades(query(trades.length))
      .then((response) => setTrades([...trades, ...response.trades]))
      .catch((error: unknown) => setProblem(message(error)))
      .finally(() => setBusy(false));
  };

  // A trade is followed by its first execution: its id changes when it is regrouped.
  const selected =
    trades.find((trade) => firstRef(trade) === selectedRef) ??
    trades.find((trade) =>
      trade.executions.some(
        (item) => `${item.ledger}:${item.execution_id}` === selectedRef,
      ),
    ) ??
    null;
  const strategies: Strategies = list?.strategies ?? [];
  const strategyName = (id: string | null) =>
    id === null
      ? t("rv.strategy.none")
      : (strategies.find((item) => item.strategy_id === id)?.name ?? id);

  const act = (work: () => Promise<unknown>) => {
    setBusy(true);
    work()
      .then(() => load())
      .catch((error: unknown) => {
        setProblem(message(error));
        setBusy(false);
      });
  };

  const checkedTrades = trades.filter((trade) => checked.has(trade.trade_id));
  const canMerge =
    checkedTrades.length > 1 &&
    checkedTrades.every((trade) => trade.rule === "manual");

  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setFilters({ ...filters, [key]: value });

  return (
    <div
      className="modal-backdrop bt-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal bt-screen rv-screen"
        role="dialog"
        aria-label={t("rv.title")}
      >
        <header className="modal-head">
          <h2>{t("rv.title")}</h2>
          <span className="workspace-note">{t("rv.subtitle")}</span>
          <button
            type="button"
            className="tb-button"
            aria-pressed={settingsOpen}
            onClick={() => setSettingsOpen(!settingsOpen)}
          >
            {t("rv.settings")}
          </button>
          <button
            type="button"
            className="tb-button"
            aria-label={t("rv.close")}
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
        {settingsOpen ? <ReviewSettingsPanel api={api} today={today} /> : null}
        <div className="rv-filters">
          <label className="bt-inline">
            {t("rv.ledger")}
            <select
              value={filters.ledger}
              onChange={(event) =>
                set("ledger", event.target.value as ReviewLedger)
              }
            >
              {LEDGERS.map((ledger) => (
                <option key={ledger} value={ledger}>
                  {t(`rv.ledger.${ledger}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="bt-inline">
            {t("rv.strategy")}
            <select
              value={filters.strategyId}
              onChange={(event) => set("strategyId", event.target.value)}
            >
              <option value="">{t("rv.all")}</option>
              <option value="none">{t("rv.strategy.none")}</option>
              {strategies.map((item) => (
                <option key={item.strategy_id} value={item.strategy_id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label className="bt-inline">
            {t("rv.status")}
            <select
              value={filters.status}
              onChange={(event) =>
                set("status", event.target.value as Filters["status"])
              }
            >
              <option value="">{t("rv.all")}</option>
              <option value="closed">{t("rv.status.closed")}</option>
              <option value="open">{t("rv.status.open")}</option>
              <option value="incomplete">{t("rv.status.incomplete")}</option>
            </select>
          </label>
          <label className="bt-inline">
            {t("rv.reviewed")}
            <select
              value={filters.reviewed}
              onChange={(event) =>
                set("reviewed", event.target.value as Filters["reviewed"])
              }
            >
              <option value="">{t("rv.all")}</option>
              <option value="yes">{t("rv.reviewed.yes")}</option>
              <option value="no">{t("rv.reviewed.no")}</option>
            </select>
          </label>
          <label className="bt-inline">
            {t("rv.tag")}
            <select
              value={filters.tag}
              onChange={(event) => set("tag", event.target.value)}
            >
              <option value="">{t("rv.all")}</option>
              {(list?.tags ?? []).map((tag) => (
                <option key={tag} value={tag}>
                  {tag}
                </option>
              ))}
            </select>
          </label>
          <label className="bt-inline">
            <input
              type="checkbox"
              checked={filters.onlyActive}
              onChange={(event) => set("onlyActive", event.target.checked)}
            />
            {t("rv.onlyActive", { symbol: symbolOf(activeInstrumentId) })}
          </label>
        </div>
        <div className="rv-body">
          <div className="rv-list">
            {list ? (
              <p className="workspace-note">
                {t("rv.count", { shown: trades.length, total: list.total })}
                {list.undated_execution_count > 0
                  ? ` · ${t("rv.undated", { n: list.undated_execution_count })}`
                  : ""}
              </p>
            ) : null}
            {checkedTrades.length > 0 ? (
              <BulkBar
                trades={checkedTrades}
                strategies={strategies}
                canMerge={canMerge}
                busy={busy}
                onAssign={(strategyId) =>
                  act(() =>
                    api.assign(
                      checkedTrades.flatMap(executionRefs),
                      strategyId,
                    ),
                  )
                }
                onMerge={() =>
                  act(() => api.makeGroup(checkedTrades.flatMap(executionRefs)))
                }
              />
            ) : null}
            {list && trades.length === 0 ? (
              <p className="workspace-note">{t("rv.empty")}</p>
            ) : null}
            <table className="bt-table rv-table" aria-label={t("rv.title")}>
              <thead>
                <tr>
                  <th aria-label={t("rv.select.all")} />
                  <th>{t("rv.col.date")}</th>
                  <th>{t("rv.col.symbol")}</th>
                  <th>{t("rv.col.account")}</th>
                  <th>{t("rv.strategy")}</th>
                  <th>{t("rv.col.pnl")}</th>
                  <th>{t("rv.col.status")}</th>
                  <th>{t("rv.col.tags")}</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade) => {
                  const label = `${symbolOf(trade.instrument_id)} ${trade.opened_on}`;
                  const net = Number(trade.net_pnl);
                  return (
                    <tr
                      key={trade.trade_id}
                      className={trade === selected ? "selected" : undefined}
                      onClick={() => setSelectedRef(firstRef(trade))}
                    >
                      <td>
                        <input
                          type="checkbox"
                          aria-label={t("rv.select", { trade: label })}
                          checked={checked.has(trade.trade_id)}
                          onClick={(event) => event.stopPropagation()}
                          onChange={(event) => {
                            const next = new Set(checked);
                            if (event.target.checked) next.add(trade.trade_id);
                            else next.delete(trade.trade_id);
                            setChecked(next);
                          }}
                        />
                      </td>
                      <td>
                        {trade.opened_on}
                        {trade.closed_on && trade.closed_on !== trade.opened_on
                          ? ` → ${trade.closed_on}`
                          : ""}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="rv-link"
                          onClick={() => setSelectedRef(firstRef(trade))}
                        >
                          {symbolOf(trade.instrument_id)}
                        </button>
                      </td>
                      <td>
                        {trade.account_ref
                          ? accountLabel(trade.account_ref, t)
                          : "—"}
                      </td>
                      <td>{strategyName(trade.strategy_id)}</td>
                      <td
                        className={net > 0 ? "rv-up" : net < 0 ? "rv-down" : ""}
                      >
                        {trade.net_pnl}
                      </td>
                      <td>
                        {t(`rv.status.${trade.status}`)}
                        {trade.annotation?.reviewed ? " ✓" : ""}
                      </td>
                      <td>{trade.annotation?.tags.join(", ") ?? ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {list && trades.length < list.total ? (
              <p className="bt-actions">
                <button
                  type="button"
                  className="tb-button"
                  disabled={busy}
                  onClick={loadMore}
                >
                  {t("rv.more")}
                </button>
              </p>
            ) : null}
          </div>
          {selected ? (
            <TradeDetail
              key={selected.trade_id}
              trade={selected}
              api={api}
              marketApi={marketApi}
              strategies={strategies}
              symbol={symbolOf(selected.instrument_id)}
              today={today}
              busy={busy}
              act={act}
              onSaved={(annotation) =>
                setTrades(
                  trades.map((trade) =>
                    trade.trade_id === selected.trade_id
                      ? { ...trade, annotation }
                      : trade,
                  ),
                )
              }
              onProblem={(error) => setProblem(message(error))}
            />
          ) : (
            <p className="workspace-note rv-pick">{t("rv.pick")}</p>
          )}
        </div>
      </section>
    </div>
  );
}

function BulkBar({
  trades,
  strategies,
  canMerge,
  busy,
  onAssign,
  onMerge,
}: {
  trades: ReviewTrade[];
  strategies: Strategies;
  canMerge: boolean;
  busy: boolean;
  onAssign: (strategyId: string | null) => void;
  onMerge: () => void;
}) {
  const { t } = useI18n();
  return (
    <div
      className="rv-bulk"
      role="group"
      aria-label={t("rv.bulk", { n: trades.length })}
    >
      <span>{t("rv.bulk", { n: trades.length })}</span>
      <label className="bt-inline">
        {t("rv.bulk.assign")}
        <select
          value=""
          disabled={busy}
          onChange={(event) => {
            const value = event.target.value;
            if (value) onAssign(value === "none" ? null : value);
          }}
        >
          <option value="">—</option>
          <option value="none">{t("rv.strategy.none")}</option>
          {strategies.map((item) => (
            <option key={item.strategy_id} value={item.strategy_id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="tb-button"
        disabled={busy || !canMerge}
        title={canMerge ? undefined : t("rv.bulk.mergeHint")}
        onClick={onMerge}
      >
        {t("rv.bulk.merge")}
      </button>
    </div>
  );
}

function TradeDetail({
  trade,
  api,
  marketApi,
  strategies,
  symbol,
  today,
  busy,
  act,
  onSaved,
  onProblem,
}: {
  trade: ReviewTrade;
  api: ReviewApi;
  marketApi: MarketApi;
  strategies: Strategies;
  symbol: string;
  today: string;
  busy: boolean;
  act: (work: () => Promise<unknown>) => void;
  onSaved: (annotation: ReviewTrade["annotation"]) => void;
  onProblem: (error: unknown) => void;
}) {
  const { t } = useI18n();
  const zone = zoneOf(trade.instrument_id);
  // Fixed for this trade: saving a note must not reload the chart.
  const [requests] = useState(() => tradeChartRequests(trade, today));
  const timeframe = requests[0]?.timeframe ?? "1d";
  const [bars, setBars] = useState<PriceBar[] | null>(null);
  const [chartProblem, setChartProblem] = useState<string | null>(null);
  const [note, setNote] = useState(trade.annotation?.note ?? "");
  const [tags, setTags] = useState(trade.annotation?.tags.join(", ") ?? "");
  const [reviewed, setReviewed] = useState(trade.annotation?.reviewed ?? false);

  useEffect(() => {
    let current = true;
    Promise.all(requests.map((request) => marketApi.bars(request)))
      .then(
        (responses) =>
          current && setBars(joinBars(responses.map((item) => item.bars))),
      )
      .catch(
        (error: unknown) =>
          current &&
          setChartProblem(
            error instanceof Error ? error.message : String(error),
          ),
      );
    return () => {
      current = false;
    };
  }, [marketApi, requests]);

  const markers = useMemo(
    () => (bars ? tradeMarkers(trade, bars, timeframe === "1d") : []),
    [bars, trade, timeframe],
  );
  const rule = strategies.find(
    (item) => item.strategy_id === trade.strategy_id,
  )?.rule;
  const groupId = manualGroupOf(trade);

  const save = () => {
    api
      .annotate(trade.trade_id, { note, tags: tagsFrom(tags), reviewed })
      .then((saved) =>
        onSaved({
          note: saved.note,
          tags: saved.tags,
          reviewed: saved.reviewed,
          recorded_at_utc: saved.recorded_at_utc,
          revision: saved.revision,
        }),
      )
      .catch(onProblem);
  };

  return (
    <div className="rv-detail" aria-label={t("rv.detail", { symbol })}>
      <h3>
        {symbol} · {trade.opened_on}
        {trade.closed_on && trade.closed_on !== trade.opened_on
          ? ` → ${trade.closed_on}`
          : ""}{" "}
        <span className="workspace-note">
          {t(`rv.status.${trade.status}`)} · {t(`rv.rule.${trade.rule}`)}
          {trade.holding_days !== null && trade.holding_days !== undefined
            ? ` · ${t("rv.holding", { n: trade.holding_days })}`
            : ""}
        </span>
      </h3>
      <div className="rv-chart-box">
        {bars ? (
          <TradeChart bars={bars} markers={markers} />
        ) : (
          <p className="workspace-note">
            {chartProblem
              ? t("rv.chartFailed", { message: chartProblem })
              : t("rv.chartLoading")}
          </p>
        )}
        <p className="workspace-note">
          {timeframe} · {t("rv.hindsight")}
          {bars && markers.length < trade.executions.length
            ? ` · ${t("rv.offChart", { n: trade.executions.length - markers.length })}`
            : ""}
        </p>
      </div>
      <dl className="rv-facts">
        <dt>{t("rv.entry")}</dt>
        <dd>
          {trade.entry_quantity}
          {trade.average_entry_price ? ` @ ${trade.average_entry_price}` : ""}
        </dd>
        <dt>{t("rv.exit")}</dt>
        <dd>
          {trade.exit_quantity}
          {trade.average_exit_price ? ` @ ${trade.average_exit_price}` : ""}
        </dd>
        <dt>{t("rv.gross")}</dt>
        <dd>{trade.gross_pnl}</dd>
        <dt>{t("rv.fees")}</dt>
        <dd>{trade.fees}</dd>
        <dt>{t("rv.net")}</dt>
        <dd>
          {trade.net_pnl} {zone.currency}
        </dd>
        {trade.open_quantity !== "0" ? (
          <>
            <dt>{t("rv.openQty")}</dt>
            <dd>{trade.open_quantity}</dd>
          </>
        ) : null}
      </dl>
      {trade.fees_unreported_count > 0 ? (
        <p className="workspace-note">
          {t("rv.feesUnreported", { n: trade.fees_unreported_count })}
        </p>
      ) : null}
      {trade.unmatched_quantity !== "0" ? (
        <p className="workspace-note">
          {t("rv.unmatched", { qty: trade.unmatched_quantity })}
        </p>
      ) : null}
      {trade.time_uncertain ? (
        <p className="workspace-note">{t("rv.timeUncertain")}</p>
      ) : null}

      <div className="rv-strategy">
        <label className="bt-inline">
          {t("rv.strategy")}
          <select
            value={trade.strategy_id ?? ""}
            disabled={busy}
            onChange={(event) =>
              act(() =>
                api.assign(executionRefs(trade), event.target.value || null),
              )
            }
          >
            <option value="">{t("rv.strategy.none")}</option>
            {strategies.map((item) => (
              <option key={item.strategy_id} value={item.strategy_id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        {trade.strategy_id && rule ? (
          <label className="bt-inline">
            {t("rv.rule")}
            <select
              value={rule}
              disabled={busy}
              onChange={(event) =>
                act(() =>
                  api.setRule(
                    trade.strategy_id as string,
                    event.target.value as ReviewRule,
                  ),
                )
              }
            >
              {RULES.map((item) => (
                <option key={item} value={item}>
                  {t(`rv.rule.${item}`)}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="workspace-note">{t("rv.rule.unassigned")}</span>
        )}
        {groupId ? (
          <button
            type="button"
            className="tb-button"
            disabled={busy}
            onClick={() => act(() => api.removeGroup(groupId))}
          >
            {t("rv.ungroup")}
          </button>
        ) : null}
      </div>

      <table className="bt-table" aria-label={t("rv.executions")}>
        <thead>
          <tr>
            <th>{t("rv.col.date")}</th>
            <th>{t("rv.side")}</th>
            <th>{t("rv.qty")}</th>
            <th>{t("rv.price")}</th>
          </tr>
        </thead>
        <tbody>
          {trade.executions.map((item) => (
            <tr key={`${item.execution_id}-${item.role}`}>
              <td>
                {item.session_date}{" "}
                {item.executed_at_utc
                  ? exchangeTime(item.executed_at_utc, zone)
                  : t("rv.timeUnknown")}
              </td>
              <td>
                {item.side} · {t(`rv.role.${item.role}`)}
              </td>
              <td>
                {item.quantity === item.fill_quantity
                  ? item.quantity
                  : t("rv.partOf", {
                      qty: item.quantity,
                      total: item.fill_quantity,
                    })}
              </td>
              <td>{item.price}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="rv-note">
        <label>
          {t("rv.note")}
          <textarea
            value={note}
            maxLength={4000}
            placeholder={t("rv.notePlaceholder")}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
        <label>
          {t("rv.tags")}
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
          />
        </label>
        <label className="bt-inline">
          <input
            type="checkbox"
            checked={reviewed}
            onChange={(event) => setReviewed(event.target.checked)}
          />
          {t("rv.markReviewed")}
        </label>
        <p className="bt-actions">
          <button type="button" className="tb-button" onClick={save}>
            {t("rv.save")}
          </button>
          {trade.annotation ? (
            <span className="workspace-note">
              {t("rv.saved", {
                at: trade.annotation.recorded_at_utc,
                revision: trade.annotation.revision,
              })}
            </span>
          ) : null}
        </p>
      </div>
      <TradeReportPanel api={api} tradeId={trade.trade_id} />
    </div>
  );
}

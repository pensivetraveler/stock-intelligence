import type { StockRankingsResponse } from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n, type MessageKey } from "./i18n";
import {
  MarketApiError,
  type InstrumentListing,
  type MarketApi,
} from "./market-api";
import {
  groupDigits,
  isInvestorRanking,
  RANKING_COUNTS,
  RANKING_DURATIONS,
  RANKING_INVESTORS,
  RANKING_KINDS,
  rankedValue,
  ratioPercent,
  supportsRealtime,
  type RankingDuration,
  type RankingInvestor,
  type RankingKind,
  type RankingMarket,
  type RankingRequest,
} from "./rankings";

interface RankingsPanelProps {
  api: MarketApi;
  activeInstrumentId: string;
  /** Show the ranked stock in the active chart (as the watchlist and search do). */
  onPick: (listing: InstrumentListing) => void;
}

/**
 * Toss stock rankings (ADR 0049): trading amount or volume, gainers and losers, and
 * Korean investors' net buying or selling. Values are shown as Toss sent them; clicking a
 * stock the symbol list knows opens it in the active chart.
 */
export function RankingsPanel({
  api,
  activeInstrumentId,
  onPick,
}: RankingsPanelProps) {
  const { t } = useI18n();
  const [kind, setKind] = useState<RankingKind>("MARKET_TRADING_AMOUNT");
  const [market, setMarket] = useState<RankingMarket>("US");
  const [duration, setDuration] = useState<RankingDuration>("1d");
  const [investor, setInvestor] = useState<RankingInvestor>("foreigner");
  const [excludeCaution, setExcludeCaution] = useState(false);
  const [count, setCount] = useState<number>(50);
  const [refresh, setRefresh] = useState(0);
  const [result, setResult] = useState<StockRankingsResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const investorRanking = isInvestorRanking(kind);
  const request: RankingRequest = investorRanking
    ? { ranking: kind, market: "KR", investor, count }
    : {
        ranking: kind,
        market,
        duration:
          duration === "realtime" && !supportsRealtime(kind) ? "1d" : duration,
        excludeCaution,
        count,
      };
  const requestKey = JSON.stringify(request);

  useEffect(() => {
    let current = true;
    setLoading(true);
    api
      .rankings(request)
      .then((next) => {
        if (!current) return;
        setResult(next);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (current) {
          setResult(null);
          setError(reason);
        }
      })
      .finally(() => current && setLoading(false));
    return () => {
      current = false;
    };
    // requestKey stands for every choice of this request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, requestKey, refresh]);

  const noKey =
    error instanceof MarketApiError &&
    error.code === "quote_source_unavailable";
  const shown = result && result.ranking === kind ? result : null;
  const valueColumn = rankedValue(kind);

  return (
    <section
      className="quote-panel rankings-panel"
      aria-label={t("rank.title")}
    >
      <h2>{t("rank.title")}</h2>
      <div className="trade-fields">
        <label>
          {t("rank.kind")}
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as RankingKind)}
          >
            {RANKING_KINDS.map((item) => (
              <option key={item} value={item}>
                {t(`rank.kind.${item}` as MessageKey)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("rank.market")}
          <select
            value={investorRanking ? "KR" : market}
            disabled={investorRanking}
            onChange={(event) => setMarket(event.target.value as RankingMarket)}
          >
            <option value="US">{t("rank.market.US")}</option>
            <option value="KR">{t("rank.market.KR")}</option>
          </select>
        </label>
        {investorRanking ? (
          <label>
            {t("rank.investor")}
            <select
              value={investor}
              onChange={(event) =>
                setInvestor(event.target.value as RankingInvestor)
              }
            >
              {RANKING_INVESTORS.map((item) => (
                <option key={item} value={item}>
                  {t(`rank.investor.${item}` as MessageKey)}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label>
            {t("rank.duration")}
            <select
              value={request.duration}
              onChange={(event) =>
                setDuration(event.target.value as RankingDuration)
              }
            >
              {RANKING_DURATIONS.map((item) => (
                <option
                  key={item}
                  value={item}
                  disabled={item === "realtime" && !supportsRealtime(kind)}
                >
                  {t(`rank.duration.${item}` as MessageKey)}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          {t("rank.count")}
          <select
            value={count}
            onChange={(event) => setCount(Number(event.target.value))}
          >
            {RANKING_COUNTS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        {investorRanking ? null : (
          <label className="wide rankings-check">
            <input
              type="checkbox"
              checked={excludeCaution}
              onChange={(event) => setExcludeCaution(event.target.checked)}
            />{" "}
            {t("rank.excludeCaution")}
          </label>
        )}
      </div>
      <p className="workspace-note">
        {t(
          investorRanking
            ? "rank.basis.investor"
            : kind.startsWith("TOSS_SECURITIES")
              ? "rank.basis.toss"
              : kind.startsWith("TOP_")
                ? "rank.basis.period"
                : "rank.basis.market",
        )}
      </p>
      {error ? (
        <p className="chart-error" role="alert">
          {noKey
            ? t("rank.noKey")
            : t("rank.failed", {
                error: error instanceof Error ? error.message : String(error),
              })}
        </p>
      ) : null}
      {loading ? (
        <p className="workspace-note">{t("research.loading")}</p>
      ) : null}
      {shown ? (
        <>
          <div className="rankings-meta">
            <p className="workspace-note">
              {shown.ranked_at_utc
                ? t("rank.meta", {
                    rows: shown.rows.length,
                    time: shown.ranked_at_utc,
                  })
                : t("rank.none")}
            </p>
            <button
              type="button"
              className="secondary"
              onClick={() => setRefresh((value) => value + 1)}
            >
              {t("rank.refresh")}
            </button>
          </div>
          {shown.rows.length ? (
            <div className="research-scroll">
              <table className="quote-trades research-table rankings-table">
                <thead>
                  <tr>
                    <th>{t("rank.col.rank")}</th>
                    <th>{t("rank.col.symbol")}</th>
                    {investorRanking ? null : (
                      <>
                        <th>{t("rank.col.last")}</th>
                        <th>{t("rank.col.change")}</th>
                      </>
                    )}
                    <th>
                      {t(`rank.col.${valueColumn}` as MessageKey)}
                      {valueColumn !== "trading_volume" &&
                      shown.rows[0].currency
                        ? ` (${shown.rows[0].currency})`
                        : ""}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown.rows.map((row) => {
                    const listing = row.listing;
                    const id = listing?.instrument.instrument_id;
                    const name = row.local_name ?? row.name;
                    const delta = row.change_rate
                      ? ratioPercent(row.change_rate)
                      : null;
                    const value = row[valueColumn];
                    return (
                      <tr
                        key={`${row.rank}-${row.symbol}`}
                        className={
                          id && id === activeInstrumentId
                            ? "selected"
                            : undefined
                        }
                      >
                        <td>{row.rank}</td>
                        <td className="rankings-symbol">
                          {listing ? (
                            <button
                              type="button"
                              className="watch-symbol"
                              aria-label={t("watch.show", {
                                symbol: row.symbol,
                              })}
                              title={name ?? undefined}
                              onClick={() => onPick(listing)}
                            >
                              {row.symbol}
                            </button>
                          ) : (
                            <span title={t("rank.unknown")}>{row.symbol}</span>
                          )}
                          {name ? <small> {name}</small> : null}
                        </td>
                        {investorRanking ? null : (
                          <>
                            <td>
                              {row.last_price
                                ? groupDigits(row.last_price)
                                : "—"}
                            </td>
                            <td
                              className={
                                delta
                                  ? delta.rising
                                    ? "legend-up"
                                    : "legend-down"
                                  : undefined
                              }
                            >
                              {delta ? delta.text : "—"}
                            </td>
                          </>
                        )}
                        <td>{value ? groupDigits(value) : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

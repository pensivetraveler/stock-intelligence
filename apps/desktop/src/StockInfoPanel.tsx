import type {
  BrokerMarketCalendar,
  ExchangeRate,
  StockInfo,
} from "@siw/contracts/types";
import { useCallback, useEffect, useState } from "react";

import { useI18n, type MessageKey } from "./i18n";
import { MarketApiError, type MarketApi } from "./market-api";
import { zoneFormat, zoneOf } from "./market-zone";

const HOUR_MINUTE: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
};

interface StockInfoPanelProps {
  api: MarketApi;
  instrumentId: string;
  symbol: string;
}

type Loaded<T> = { value: T | null; error: unknown };

/**
 * Broker reference data for the active chart's stock (names, ISIN, listing, shares,
 * warnings), the USD/KRW reference rate, and today's US market hours with any session
 * whose bounds differ from the calendar the charts use. Read on demand from Toss.
 */
export function StockInfoPanel({
  api,
  instrumentId,
  symbol,
}: StockInfoPanelProps) {
  const { t } = useI18n();
  const zone = zoneOf(instrumentId);
  const et = (utc: string) =>
    zoneFormat(zone, "en-US", HOUR_MINUTE).format(new Date(utc));
  const [info, setInfo] = useState<Loaded<StockInfo>>({
    value: null,
    error: null,
  });
  const [rate, setRate] = useState<Loaded<ExchangeRate>>({
    value: null,
    error: null,
  });
  const [hours, setHours] = useState<Loaded<BrokerMarketCalendar>>({
    value: null,
    error: null,
  });

  const load = useCallback(() => {
    let current = true;
    const track =
      <T,>(set: (loaded: Loaded<T>) => void) =>
      (promise: Promise<T>) =>
        promise
          .then((value) => current && set({ value, error: null }))
          .catch((error: unknown) => current && set({ value: null, error }));
    setInfo({ value: null, error: null });
    track<StockInfo>(setInfo)(api.stockInfo(instrumentId));
    track<ExchangeRate>(setRate)(api.exchangeRate());
    track<BrokerMarketCalendar>(setHours)(
      api.brokerCalendar(zoneOf(instrumentId).market),
    );
    return () => {
      current = false;
    };
  }, [api, instrumentId]);

  useEffect(() => load(), [load]);

  const errors = [info.error, rate.error, hours.error].filter(Boolean);
  const noKey = errors.some(
    (error) =>
      error instanceof MarketApiError &&
      error.code === "quote_source_unavailable",
  );
  const stock = info.value;
  const today = hours.value?.days.find((day) => day.role === "today");
  return (
    <section className="quote-panel" aria-label={t("info.title")}>
      <h2>
        {t("info.title")} · {symbol}
      </h2>
      {noKey ? (
        <p className="chart-error" role="alert">
          {t("info.noKey")}
        </p>
      ) : errors.length > 0 ? (
        <p className="chart-error" role="alert">
          {t("info.failed", {
            error: errors
              .map((error) =>
                error instanceof Error ? error.message : String(error),
              )
              .join(" · "),
          })}
        </p>
      ) : null}
      {stock ? (
        <dl className="info-list">
          <dt>{t("info.name")}</dt>
          <dd>
            {stock.name ?? "—"}
            {stock.english_name ? ` (${stock.english_name})` : ""}
          </dd>
          <dt>{t("info.market")}</dt>
          <dd>
            {[stock.market, stock.security_type, stock.status]
              .filter(Boolean)
              .join(" · ")}
          </dd>
          <dt>ISIN</dt>
          <dd>{stock.isin ?? "—"}</dd>
          <dt>{t("info.listed")}</dt>
          <dd>
            {stock.list_date ?? "—"}
            {stock.delist_date
              ? ` → ${t("info.delisted", { date: stock.delist_date })}`
              : ""}
          </dd>
          <dt>{t("info.shares")}</dt>
          <dd>{stock.shares_outstanding ?? "—"}</dd>
          {stock.leverage_factor ? (
            <>
              <dt>{t("info.leverage")}</dt>
              <dd>{stock.leverage_factor}×</dd>
            </>
          ) : null}
          <dt>{t("info.warnings")}</dt>
          <dd>
            {stock.warnings.length === 0
              ? t("info.noWarnings")
              : stock.warnings.map((warning) => (
                  <div key={`${warning.type}-${warning.start_date}`}>
                    <strong>{warning.type}</strong> {warning.start_date ?? ""}
                    {warning.end_date ? ` – ${warning.end_date}` : ""}
                  </div>
                ))}
          </dd>
        </dl>
      ) : null}
      {rate.value ? (
        <p>
          {t("info.rate", {
            rate: rate.value.rate,
            change: rate.value.change ?? "",
          })}
        </p>
      ) : null}
      {today ? (
        <>
          <h3>
            {t(zone.market === "KR" ? "info.hoursKr" : "info.hours", {
              date: today.date,
            })}
          </h3>
          <table className="quote-trades">
            <tbody>
              {today.sessions.map((session) => (
                <tr key={session.name}>
                  <td>{t(`info.session.${session.name}` as MessageKey)}</td>
                  <td>
                    {et(session.start_utc)} – {et(session.end_utc)} {zone.label}
                  </td>
                  <td>
                    {session.agrees === false && session.local_start_utc
                      ? t("info.differs", {
                          start: et(session.local_start_utc),
                          end: et(session.local_end_utc ?? session.end_utc),
                        })
                      : session.agrees
                        ? "✓"
                        : ""}
                  </td>
                </tr>
              ))}
              {today.sessions.length === 0 ? (
                <tr>
                  <td>{t("info.closed")}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </>
      ) : null}
      <p className="workspace-note">
        {t("info.source")}{" "}
        <button type="button" className="secondary" onClick={() => load()}>
          {t("info.refresh")}
        </button>
      </p>
    </section>
  );
}

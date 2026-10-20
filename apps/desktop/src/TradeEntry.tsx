import type { PriceBar } from "@siw/contracts/types";
import { useState, type FormEvent } from "react";

import { exchangeTime } from "./chart-data";
import { zoneOf } from "./market-zone";
import { useI18n, type Translate } from "./i18n";
import {
  MarketApiError,
  type ManualExecutionRequest,
  type MarketApi,
} from "./market-api";

type Mode = "manual" | "hypothetical";

interface TradeEntryProps {
  api: MarketApi;
  bar: PriceBar;
  symbol: string;
  sessionDate: string;
  newId?: () => string;
  onRecorded: () => void;
  onClose: () => void;
}

function message(t: Translate, error: unknown): string {
  if (error instanceof MarketApiError) {
    return error.code === "journal_storage_unconfigured"
      ? t("trade.journalOff")
      : error.message;
  }
  return error instanceof Error ? error.message : t("common.requestFailed");
}

/**
 * Records an actual (user-asserted) execution or a hypothetical scenario from a clicked
 * candle. The candle is shown as context only: price and time are entered separately.
 */
export function TradeEntry({
  api,
  bar,
  symbol,
  sessionDate,
  newId = () => crypto.randomUUID(),
  onRecorded,
  onClose,
}: TradeEntryProps) {
  const { t } = useI18n();
  const [mode, setMode] = useState<Mode>("manual");
  // One ID per open form, so a double submit or retry cannot create a second record.
  const [recordId] = useState(newId);
  const [status, setStatus] = useState<{
    error: string | null;
    saving: boolean;
  }>({
    error: null,
    saving: false,
  });
  const [manual, setManual] = useState({
    side: "buy" as ManualExecutionRequest["side"],
    quantity: "",
    price: "",
    fees: "0",
    precision: "minute" as ManualExecutionRequest["time_precision"],
    date: sessionDate,
    time: "",
    memo: "",
  });
  const [scenario, setScenario] = useState({
    direction: "long" as "long" | "short",
    quantity: "",
    entryPrice: "",
    exitPrice: "",
    exitTime: "",
    fees: "0",
    slippage: "0",
  });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setStatus({ error: null, saving: true });
    try {
      if (mode === "manual") {
        const unknownTime = manual.precision === "unknown";
        await api.recordManual({
          execution_id: recordId,
          instrument_id: bar.instrument_id,
          side: manual.side,
          quantity: manual.quantity,
          price: manual.price,
          fees: manual.fees,
          time_precision: manual.precision,
          executed_date_et: unknownTime ? null : manual.date,
          executed_time_et: unknownTime ? null : manual.time,
          selected_interval_start_utc: bar.interval_start_utc,
          selected_interval_end_utc: bar.interval_end_utc,
          memo: manual.memo,
        });
      } else {
        await api.recordScenario({
          scenario_id: recordId,
          instrument_id: bar.instrument_id,
          direction: scenario.direction,
          quantity: scenario.quantity,
          entry_price: scenario.entryPrice,
          exit_price: scenario.exitPrice,
          fee_assumption: scenario.fees,
          slippage_assumption: scenario.slippage,
          entry_interval_start_utc: bar.interval_start_utc,
          exit_date_et: sessionDate,
          exit_time_et: scenario.exitTime,
        });
      }
      onRecorded();
      onClose();
    } catch (error) {
      setStatus({ error: message(t, error), saving: false });
    }
  };

  const zone = zoneOf(bar.instrument_id);
  const candle = `${exchangeTime(bar.interval_start_utc, zone)}–${exchangeTime(bar.interval_end_utc, zone)}`;

  return (
    <form
      className="trade-entry"
      onSubmit={(event) => void submit(event)}
      aria-label={t("trade.form")}
    >
      <div className="trade-entry-head">
        <div role="tablist" aria-label={t("trade.recordType")}>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "manual"}
            className={mode === "manual" ? "" : "secondary"}
            onClick={() => setMode("manual")}
          >
            {t("trade.manualTab")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "hypothetical"}
            className={mode === "hypothetical" ? "" : "secondary"}
            onClick={() => setMode("hypothetical")}
          >
            {t("trade.hypoTab")}
          </button>
        </div>
        <p className="trade-context">
          {t("trade.context", {
            symbol,
            candle,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
          })}
        </p>
      </div>

      {mode === "manual" ? (
        <div className="trade-fields">
          <label>
            {t("trade.side")}
            <select
              value={manual.side}
              onChange={(e) =>
                setManual({ ...manual, side: e.target.value as "buy" | "sell" })
              }
            >
              <option value="buy">{t("trade.buy")}</option>
              <option value="sell">{t("trade.sell")}</option>
            </select>
          </label>
          <label>
            {t("trade.quantity")}
            <input
              required
              inputMode="decimal"
              value={manual.quantity}
              onChange={(e) =>
                setManual({ ...manual, quantity: e.target.value.trim() })
              }
            />
          </label>
          <label>
            {t("trade.fillPrice")}
            <input
              required
              inputMode="decimal"
              value={manual.price}
              onChange={(e) =>
                setManual({ ...manual, price: e.target.value.trim() })
              }
            />
          </label>
          <label>
            {t("trade.fees")}
            <input
              required
              inputMode="decimal"
              value={manual.fees}
              onChange={(e) =>
                setManual({ ...manual, fees: e.target.value.trim() })
              }
            />
          </label>
          <label>
            {t("trade.precision")}
            <select
              value={manual.precision}
              onChange={(e) =>
                setManual({
                  ...manual,
                  precision: e.target
                    .value as ManualExecutionRequest["time_precision"],
                })
              }
            >
              <option value="exact">{t("trade.exact")}</option>
              <option value="minute">{t("trade.minute")}</option>
              <option value="approximate">{t("trade.approximate")}</option>
              <option value="unknown">{t("trade.unknown")}</option>
            </select>
          </label>
          {manual.precision === "unknown" ? null : (
            <>
              <label>
                {t("trade.fillDate", { zone: zone.label })}
                <input
                  required
                  type="date"
                  value={manual.date}
                  onChange={(e) =>
                    setManual({ ...manual, date: e.target.value })
                  }
                />
              </label>
              <label>
                {t("trade.fillTime", { zone: zone.label })}
                <input
                  required
                  type="time"
                  step={manual.precision === "exact" ? 1 : 60}
                  value={manual.time}
                  onChange={(e) =>
                    setManual({ ...manual, time: e.target.value })
                  }
                />
              </label>
            </>
          )}
          <label className="wide">
            {t("trade.memo")}
            <input
              maxLength={500}
              value={manual.memo}
              onChange={(e) => setManual({ ...manual, memo: e.target.value })}
            />
          </label>
        </div>
      ) : (
        <div className="trade-fields">
          <label>
            {t("trade.direction")}
            <select
              value={scenario.direction}
              onChange={(e) =>
                setScenario({
                  ...scenario,
                  direction: e.target.value as "long" | "short",
                })
              }
            >
              <option value="long">{t("trade.long")}</option>
              <option value="short">{t("trade.short")}</option>
            </select>
          </label>
          <label>
            {t("trade.quantity")}
            <input
              required
              inputMode="decimal"
              value={scenario.quantity}
              onChange={(e) =>
                setScenario({ ...scenario, quantity: e.target.value.trim() })
              }
            />
          </label>
          <label>
            {t("trade.entryPrice")}
            <input
              required
              inputMode="decimal"
              value={scenario.entryPrice}
              onChange={(e) =>
                setScenario({ ...scenario, entryPrice: e.target.value.trim() })
              }
            />
          </label>
          <label>
            {t("trade.exitPrice")}
            <input
              required
              inputMode="decimal"
              value={scenario.exitPrice}
              onChange={(e) =>
                setScenario({ ...scenario, exitPrice: e.target.value.trim() })
              }
            />
          </label>
          <label>
            {t("trade.exitTime", { date: sessionDate, zone: zone.label })}
            <input
              required
              type="time"
              value={scenario.exitTime}
              onChange={(e) =>
                setScenario({ ...scenario, exitTime: e.target.value })
              }
            />
          </label>
          <label>
            {t("trade.feesTotal")}
            <input
              required
              inputMode="decimal"
              value={scenario.fees}
              onChange={(e) =>
                setScenario({ ...scenario, fees: e.target.value.trim() })
              }
            />
          </label>
          <label>
            {t("trade.slippage")}
            <input
              required
              inputMode="decimal"
              value={scenario.slippage}
              onChange={(e) =>
                setScenario({ ...scenario, slippage: e.target.value.trim() })
              }
            />
          </label>
        </div>
      )}

      <div className="trade-actions">
        <button type="submit" disabled={status.saving}>
          {mode === "manual"
            ? t("trade.recordManual")
            : t("trade.saveScenario")}
        </button>
        <button type="button" className="secondary" onClick={onClose}>
          {t("trade.cancel")}
        </button>
        {status.error ? (
          <span className="chart-error" role="alert">
            {status.error}
          </span>
        ) : null}
      </div>
    </form>
  );
}

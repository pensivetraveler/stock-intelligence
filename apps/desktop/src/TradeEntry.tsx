import type { PriceBar } from "@siw/contracts/types";
import { useState, type FormEvent } from "react";

import { exchangeTime } from "./chart-data";
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

function message(error: unknown): string {
  if (error instanceof MarketApiError) {
    return error.code === "journal_storage_unconfigured"
      ? "The trade journal is off: start the API with SIW_DATA_DIR set. Nothing was saved."
      : error.message;
  }
  return error instanceof Error ? error.message : "Request failed";
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
      setStatus({ error: message(error), saving: false });
    }
  };

  const candle = `${exchangeTime(bar.interval_start_utc)}–${exchangeTime(bar.interval_end_utc)}`;

  return (
    <form
      className="trade-entry"
      onSubmit={(event) => void submit(event)}
      aria-label="Trade entry"
    >
      <div className="trade-entry-head">
        <div role="tablist" aria-label="Record type">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "manual"}
            className={mode === "manual" ? "" : "secondary"}
            onClick={() => setMode("manual")}
          >
            Actual execution (manual)
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "hypothetical"}
            className={mode === "hypothetical" ? "" : "secondary"}
            onClick={() => setMode("hypothetical")}
          >
            Hypothetical scenario
          </button>
        </div>
        <p className="trade-context">
          {symbol} · selected candle {candle} · O {bar.open} H {bar.high} L{" "}
          {bar.low} C {bar.close}. Context only: the candle is not your fill
          time or price.
        </p>
      </div>

      {mode === "manual" ? (
        <div className="trade-fields">
          <label>
            Side
            <select
              value={manual.side}
              onChange={(e) =>
                setManual({ ...manual, side: e.target.value as "buy" | "sell" })
              }
            >
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
            </select>
          </label>
          <label>
            Quantity
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
            Fill price (USD)
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
            Fees (USD)
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
            Time precision
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
              <option value="exact">Exact (seconds)</option>
              <option value="minute">To the minute</option>
              <option value="approximate">Approximate</option>
              <option value="unknown">Unknown</option>
            </select>
          </label>
          {manual.precision === "unknown" ? null : (
            <>
              <label>
                Fill date (ET)
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
                Fill time (ET)
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
            Memo (do not enter account numbers or credentials)
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
            Direction
            <select
              value={scenario.direction}
              onChange={(e) =>
                setScenario({
                  ...scenario,
                  direction: e.target.value as "long" | "short",
                })
              }
            >
              <option value="long">Long</option>
              <option value="short">Short</option>
            </select>
          </label>
          <label>
            Quantity
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
            Entry price
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
            Exit price
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
            Exit time (ET, {sessionDate})
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
            Fees, total (USD)
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
            Slippage per share
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
            ? "Record actual execution"
            : "Save hypothetical scenario"}
        </button>
        <button type="button" className="secondary" onClick={onClose}>
          Cancel
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

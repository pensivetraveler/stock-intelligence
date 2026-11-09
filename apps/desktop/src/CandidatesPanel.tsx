import type {
  BacktestStrategy,
  ScreeningCandidates,
  ScreeningCheck,
} from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n } from "./i18n";
import type { InstrumentListing } from "./market-api";
import type { ScreeningApi } from "./screening-api";

const STRATEGY_KEY = "siw.screening.strategy";

interface CandidatesPanelProps {
  api: ScreeningApi;
  /** Saved strategies; only rules strategies can be checked on daily bars. */
  strategies: () => Promise<BacktestStrategy[]>;
  onPick: (listing: InstrumentListing) => void;
}

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

const STATUSES = [
  "passed",
  "failed",
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

  useEffect(() => {
    let current = true;
    setLoadError(null);
    api
      .candidates()
      .then((found) => current && setDocument(found))
      .catch((reason: unknown) => current && setLoadError(reason));
    return () => {
      current = false;
    };
  }, [api, reload]);

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
  const showDetail = (checkId: number) => {
    api
      .check(checkId)
      .then(setDetail)
      .catch((reason: unknown) => setRunError(errorText(reason)));
  };

  const chartable = (document?.candidates ?? []).filter(
    (item) => item.listing !== null,
  );
  const coverage = document?.watch_coverage;

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
          onClick={() => setReload(reload + 1)}
        >
          {t("candidates.refresh")}
        </button>
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
                <th>{t("candidates.entry")}</th>
              </tr>
            </thead>
            <tbody>
              {document.candidates.map((item) => {
                const listing = item.listing;
                const entry = item.checks.entry;
                return (
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
                    <td title={item.first_seen_utc}>
                      {newYorkTime(item.first_seen_utc)}
                    </td>
                    <td>
                      {item.in_list === null
                        ? t("candidates.manual")
                        : item.in_list
                          ? t("candidates.yes")
                          : t("candidates.gone")}
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
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : document ? (
        <p className="workspace-note">{t("candidates.empty")}</p>
      ) : null}
      {detail ? (
        <EntryDetail check={detail} onClose={() => setDetail(null)} />
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

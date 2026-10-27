import type {
  ReviewPeriod,
  ReviewPeriodList,
  ReviewPeriodSummary,
} from "@siw/contracts/types";
import { useCallback, useEffect, useState } from "react";

import { accountLabel } from "./account-label";
import { useI18n, type MessageKey } from "./i18n";
import { PeriodCurve } from "./PeriodCurve";
import {
  daysBetween,
  presetRange,
  weekdayOf,
  type Preset,
} from "./period-review";
import type { ReviewApi, ReviewLedger } from "./review-api";

type Section = ReviewPeriod["sections"][number];

const PRESETS: Preset[] = ["thisWeek", "lastWeek", "thisMonth", "lastMonth"];
const SUMMARY_ROWS: (keyof ReviewPeriodSummary)[] = [
  "trades",
  "win_rate",
  "net_pnl",
  "average_win",
  "average_loss",
  "profit_factor",
  "expectancy",
  "fees",
  "max_drawdown",
  "longest_losing_streak",
  "largest_loss_share",
];
const GROUPS = [
  "tag",
  "ticker",
  "time_of_day",
  "weekday",
  "holding",
  "size",
] as const;

/**
 * Period review (ADR 0042, step 4a): statistics per currency for trades closed in a
 * period, against the period just before. Saved, so earlier reviews can be reopened.
 * Nothing here calls Claude.
 */
export function PeriodReview({
  api,
  today,
  strategies,
}: {
  api: ReviewApi;
  today: string;
  strategies: { strategy_id: string; name: string }[];
}) {
  const { t } = useI18n();
  const [preset, setPreset] = useState<Preset | "custom">("lastWeek");
  const [range, setRange] = useState(() => presetRange("lastWeek", today));
  const [ledger, setLedger] = useState<ReviewLedger>("broker");
  const [account, setAccount] = useState("");
  const [strategy, setStrategy] = useState("");
  const [accounts, setAccounts] = useState<string[]>([]);
  const [saved, setSaved] = useState<ReviewPeriodList["reviews"]>([]);
  const [shown, setShown] = useState<ReviewPeriod | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const fail = useCallback(
    (error: unknown) =>
      setProblem(
        error instanceof Error ? error.message : t("common.requestFailed"),
      ),
    [t],
  );

  useEffect(() => {
    api
      .periodReviews()
      .then((list) => setSaved(list.reviews))
      .catch(fail);
    api
      .capital()
      .then((loaded) =>
        setAccounts([
          ...new Set(loaded.accounts.map((item) => item.account_ref)),
        ]),
      )
      .catch(() => undefined);
  }, [api, fail]);

  const make = () => {
    setBusy(true);
    setProblem(null);
    api
      .makePeriodReview({
        fromDate: range[0],
        toDate: range[1],
        ledger,
        ...(account ? { accountRef: account } : {}),
        ...(strategy ? { strategyId: strategy } : {}),
      })
      .then((review) => {
        setShown(review);
        return api.periodReviews().then((list) => setSaved(list.reviews));
      })
      .catch(fail)
      .finally(() => setBusy(false));
  };

  return (
    <div className="rv-period">
      <aside className="rv-period-side">
        <div className="rv-period-form" aria-label={t("pd.make")}>
          <label className="bt-inline">
            {t("pd.period")}
            <select
              value={preset}
              onChange={(event) => {
                const value = event.target.value as Preset | "custom";
                setPreset(value);
                if (value !== "custom") setRange(presetRange(value, today));
              }}
            >
              {PRESETS.map((item) => (
                <option key={item} value={item}>
                  {t(`pd.preset.${item}`)}
                </option>
              ))}
              <option value="custom">{t("pd.preset.custom")}</option>
            </select>
          </label>
          <label className="bt-inline">
            {t("ck.from")}
            <input
              type="date"
              value={range[0]}
              onChange={(event) => {
                setPreset("custom");
                setRange([event.target.value, range[1]]);
              }}
            />
          </label>
          <label className="bt-inline">
            {t("ck.to")}
            <input
              type="date"
              value={range[1]}
              onChange={(event) => {
                setPreset("custom");
                setRange([range[0], event.target.value]);
              }}
            />
          </label>
          <label className="bt-inline">
            {t("rv.ledger")}
            <select
              value={ledger}
              onChange={(event) =>
                setLedger(event.target.value as ReviewLedger)
              }
            >
              {(["broker", "imported", "manual"] as const).map((item) => (
                <option key={item} value={item}>
                  {t(`rv.ledger.${item}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="bt-inline">
            {t("rv.col.account")}
            <select
              value={account}
              onChange={(event) => setAccount(event.target.value)}
            >
              <option value="">{t("rv.all")}</option>
              {accounts.map((ref) => (
                <option key={ref} value={ref}>
                  {accountLabel(ref, t)}
                </option>
              ))}
            </select>
          </label>
          <label className="bt-inline">
            {t("rv.strategy")}
            <select
              value={strategy}
              onChange={(event) => setStrategy(event.target.value)}
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
          <p className="bt-actions">
            <button
              type="button"
              className="tb-button"
              disabled={busy || !range[0] || !range[1]}
              onClick={make}
            >
              {busy ? t("pd.making") : t("pd.make")}
            </button>
          </p>
          {problem ? (
            <p className="bt-problem" role="alert">
              {problem}
            </p>
          ) : null}
        </div>
        <h4>{t("pd.saved")}</h4>
        <ul className="rv-period-list" aria-label={t("pd.saved")}>
          {saved.map((item) => (
            <li key={item.review_id}>
              <button
                type="button"
                className="rv-link"
                onClick={() =>
                  api.periodReview(item.review_id).then(setShown).catch(fail)
                }
              >
                {item.from_date} → {item.to_date}
              </button>{" "}
              <span className="workspace-note">
                {item.totals
                  .map((total) => `${total.net_pnl} ${total.currency}`)
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      </aside>
      <div className="rv-period-main">
        {shown ? (
          <PeriodDocument review={shown} />
        ) : (
          <p className="workspace-note">{t("pd.pick")}</p>
        )}
      </div>
    </div>
  );
}

function PeriodDocument({ review }: { review: ReviewPeriod }) {
  const { t } = useI18n();
  return (
    <article aria-label={t("pd.review")}>
      <h3>
        {review.from_date} → {review.to_date}{" "}
        <span className="workspace-note">
          {t("pd.previous", {
            from: review.previous_from_date,
            to: review.previous_to_date,
          })}{" "}
          · {t("pd.made", { at: review.created_at_utc })}
        </span>
      </h3>
      {review.sections.length === 0 ? (
        <p className="workspace-note">{t("pd.empty")}</p>
      ) : null}
      {review.sections.map((section) => (
        <CurrencySection key={section.currency} section={section} />
      ))}
    </article>
  );
}

function change(
  now: string | number | null,
  before: string | number | null,
): string {
  if (now === null || before === null) return "";
  const delta = Number(now) - Number(before);
  if (!Number.isFinite(delta) || delta === 0) return "";
  return `${delta > 0 ? "▲" : "▼"} ${Math.abs(delta).toLocaleString(undefined, {
    maximumFractionDigits: 4,
  })}`;
}

function CurrencySection({ section }: { section: Section }) {
  const { t } = useI18n();
  const { principles, what_if: whatIf } = section;
  return (
    <section className="rv-period-section" aria-label={section.currency}>
      <h4>{section.currency}</h4>
      <table className="bt-table" aria-label={t("pd.summary")}>
        <thead>
          <tr>
            <th />
            <th>{t("pd.thisPeriod")}</th>
            <th>{t("pd.previousPeriod")}</th>
            <th>{t("pd.change")}</th>
          </tr>
        </thead>
        <tbody>
          {SUMMARY_ROWS.map((key) => (
            <tr key={key}>
              <td>{t(`pd.stat.${key}`)}</td>
              <td>{section.summary[key] ?? "—"}</td>
              <td>{section.previous_summary[key] ?? "—"}</td>
              <td>
                {change(section.summary[key], section.previous_summary[key])}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {section.curve.length > 0 ? <PeriodCurve points={section.curve} /> : null}
      <h5>{t("pc.title")}</h5>
      <p className="workspace-note">
        {t("pd.checked", {
          checked: principles.checked,
          unchecked: principles.unchecked,
        })}
      </p>
      {principles.items.length > 0 ? (
        <table className="bt-table" aria-label={t("pc.title")}>
          <tbody>
            {principles.items.map((item) => (
              <tr key={item.item_id}>
                <td>{item.text || t(`pr.kind.${item.kind}` as MessageKey)}</td>
                <td>{t("pd.brokenTimes", { n: item.broken })}</td>
                <td>{t("pd.keptTimes", { n: item.kept })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {principles.checked > 0 ? (
        <p>
          {t("pd.brokenVsKept", {
            broken: principles.broken_trades,
            brokenNet: principles.broken_net_pnl,
            kept: principles.kept_trades,
            keptNet: principles.kept_net_pnl,
          })}
        </p>
      ) : null}
      <h5>
        {t("wi.principles")}{" "}
        <span className="workspace-note">{t("rp.hindsight")}</span>
      </h5>
      <p>
        {whatIf.computed > 0
          ? t("pd.whatIf", {
              hypothetical: whatIf.gross_pnl,
              actual: whatIf.actual_gross_pnl,
              difference: whatIf.difference,
            })
          : t("wi.none")}
        {whatIf.missing > 0
          ? ` · ${t("pd.missing", { n: whatIf.missing })}`
          : ""}
      </p>
      {GROUPS.map((name) => (
        <GroupTable key={name} name={name} rows={section.groups[name]} />
      ))}
      <DailyCalendar daily={section.daily} />
    </section>
  );
}

function GroupTable({
  name,
  rows,
}: {
  name: (typeof GROUPS)[number];
  rows: Section["groups"]["tag"];
}) {
  const { t } = useI18n();
  if (rows.length === 0) return null;
  const largest = Math.max(
    ...rows.map((row) => Math.abs(Number(row.net_pnl))),
    1,
  );
  const label = (key: string) =>
    name === "time_of_day" || name === "weekday"
      ? t(`pd.key.${key}` as MessageKey)
      : key;
  return (
    <table className="bt-table rv-group" aria-label={t(`pd.group.${name}`)}>
      <thead>
        <tr>
          <th>{t(`pd.group.${name}`)}</th>
          <th>{t("rv.col.pnl")}</th>
          <th />
          <th>{t("pd.stat.trades")}</th>
          <th>{t("pd.stat.win_rate")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const net = Number(row.net_pnl);
          return (
            <tr key={row.key}>
              <td>{label(row.key)}</td>
              <td className={net > 0 ? "rv-up" : net < 0 ? "rv-down" : ""}>
                {row.net_pnl}
              </td>
              <td className="rv-bar-cell">
                <span
                  className={`rv-bar ${net < 0 ? "rv-bar-down" : "rv-bar-up"}`}
                  style={{ width: `${(Math.abs(net) / largest) * 100}%` }}
                />
              </td>
              <td>{row.trades}</td>
              <td>{row.win_rate ?? "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function DailyCalendar({ daily }: { daily: Section["daily"] }) {
  const { t } = useI18n();
  if (daily.length === 0) return null;
  const byDay = new Map(daily.map((day) => [day.date, day]));
  const days = daysBetween(daily[0]!.date, daily[daily.length - 1]!.date);
  const largest = Math.max(
    ...daily.map((day) => Math.abs(Number(day.net_pnl))),
    1,
  );
  return (
    <div className="rv-calendar" aria-label={t("pd.calendar")}>
      <h5>{t("pd.calendar")}</h5>
      <div className="rv-calendar-grid">
        {Array.from({ length: weekdayOf(days[0]!) }, (_, index) => (
          <span key={`pad-${index}`} />
        ))}
        {days.map((day) => {
          const entry = byDay.get(day);
          const net = entry ? Number(entry.net_pnl) : 0;
          const strength = entry ? 0.2 + (0.8 * Math.abs(net)) / largest : 0;
          const color =
            net >= 0
              ? `rgba(63, 182, 139, ${strength})`
              : `rgba(229, 86, 107, ${strength})`;
          return (
            <span
              key={day}
              className="rv-day"
              style={entry ? { background: color } : undefined}
              title={
                entry
                  ? t("pd.day", {
                      date: day,
                      net: entry.net_pnl,
                      trades: entry.trades,
                    })
                  : day
              }
            >
              {day.slice(8)}
            </span>
          );
        })}
      </div>
    </div>
  );
}

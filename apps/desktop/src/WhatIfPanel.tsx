import type {
  ReviewStrategySuggestions,
  ReviewWhatIf,
} from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n } from "./i18n";
import type { ReviewApi } from "./review-api";

type Part = NonNullable<ReviewWhatIf["principles"]>;

/**
 * Hindsight replays of one trade (ADR 0042, step 3b): by the principles' stop and target,
 * and by a saved strategy (the linked one, or one picked here). For a trade without a
 * strategy, saved rule strategies whose entry held just before the buy are suggested.
 */
export function WhatIfPanel({
  api,
  tradeId,
  linkedStrategyId,
  strategies,
  onAssign,
}: {
  api: ReviewApi;
  tradeId: string;
  linkedStrategyId: string | null;
  strategies: { strategy_id: string; name: string }[];
  onAssign: (strategyId: string) => void;
}) {
  const { t } = useI18n();
  const [strategyId, setStrategyId] = useState(linkedStrategyId ?? "");
  const [result, setResult] = useState<ReviewWhatIf | null>(null);
  const [suggested, setSuggested] = useState<ReviewStrategySuggestions | null>(
    null,
  );
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setResult(null);
    api
      .whatIf(tradeId, strategyId || undefined)
      .then((loaded) => current && setResult(loaded))
      .catch(
        (error: unknown) =>
          current &&
          setProblem(error instanceof Error ? error.message : String(error)),
      );
    return () => {
      current = false;
    };
  }, [api, tradeId, strategyId]);

  useEffect(() => {
    if (linkedStrategyId) return;
    let current = true;
    api
      .strategySuggestions(tradeId)
      .then((loaded) => current && setSuggested(loaded))
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [api, tradeId, linkedStrategyId]);

  return (
    <section className="rv-whatif" aria-label={t("wi.title")}>
      <h4>
        {t("wi.title")}{" "}
        <span className="workspace-note">{t("rp.hindsight")}</span>
      </h4>
      {problem ? <p className="workspace-note">{problem}</p> : null}
      {suggested && suggested.suggestions.length > 0 ? (
        <div className="rv-suggestions" aria-label={t("wi.suggested")}>
          <span className="workspace-note">{t("wi.suggested")}</span>
          {suggested.suggestions.map((item) => (
            <p key={item.strategy_id} className="bt-actions">
              {item.name} ({item.timeframes.join(", ")})
              <button
                type="button"
                className="tb-button"
                onClick={() => onAssign(item.strategy_id)}
              >
                {t("wi.assign")}
              </button>
            </p>
          ))}
        </div>
      ) : null}
      {!result ? (
        <p className="workspace-note">{t("rv.chartLoading")}</p>
      ) : (
        <>
          <WhatIfPart title={t("wi.principles")} part={result.principles} />
          <label className="bt-inline">
            {t("wi.strategy")}
            <select
              value={strategyId}
              onChange={(event) => setStrategyId(event.target.value)}
            >
              <option value="">—</option>
              {strategies.map((item) => (
                <option key={item.strategy_id} value={item.strategy_id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <WhatIfPart title={t("wi.byStrategy")} part={result.strategy} />
        </>
      )}
    </section>
  );
}

function WhatIfPart({ title, part }: { title: string; part: Part | null }) {
  const { t } = useI18n();
  if (part === null) {
    return (
      <p className="workspace-note">
        {title}: {t("wi.none")}
      </p>
    );
  }
  if (part.status !== "computed") {
    return (
      <p className="workspace-note">
        {title}: {t(`wi.status.${part.status}`)}
        {part.detail ? ` — ${part.detail}` : ""}
      </p>
    );
  }
  const difference = Number(part.difference ?? "0");
  return (
    <div className="rv-whatif-part">
      <p>
        <strong>{title}</strong>
        {part.reason ? ` · ${t(`wi.reason.${part.reason}`)}` : ""}
      </p>
      <dl className="rv-facts">
        {part.entry_price ? (
          <>
            <dt>{t("rv.entry")}</dt>
            <dd>
              {part.entry_price} · {part.entry_at_utc}
            </dd>
          </>
        ) : null}
        {part.exit_price ? (
          <>
            <dt>{t("rv.exit")}</dt>
            <dd>
              {part.exit_price} · {part.exit_at_utc}
            </dd>
          </>
        ) : null}
        <dt>{t("wi.pnl")}</dt>
        <dd>{part.gross_pnl}</dd>
        <dt>{t("wi.actual")}</dt>
        <dd>{part.actual_gross_pnl}</dd>
        <dt>{t("wi.difference")}</dt>
        <dd
          className={difference > 0 ? "rv-up" : difference < 0 ? "rv-down" : ""}
        >
          {part.difference}
        </dd>
      </dl>
      {part.notes.length > 0 ? (
        <p className="workspace-note">{part.notes.join(" · ")}</p>
      ) : null}
    </div>
  );
}

import type { ReviewPrincipleCheck } from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n } from "./i18n";
import type { ReviewApi } from "./review-api";

/** How one trade measured up to the principles in effect when it opened (step 3a). */
export function PrincipleCheck({
  api,
  tradeId,
  strategies,
}: {
  api: ReviewApi;
  tradeId: string;
  strategies: { strategy_id: string; name: string }[];
}) {
  const { t } = useI18n();
  const [check, setCheck] = useState<ReviewPrincipleCheck | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api
      .principleCheck(tradeId)
      .then((loaded) => current && setCheck(loaded))
      .catch(
        (error: unknown) =>
          current &&
          setProblem(error instanceof Error ? error.message : String(error)),
      );
    return () => {
      current = false;
    };
  }, [api, tradeId]);

  const scopeName = (scope: string) =>
    scope === "shared"
      ? t("pr.shared")
      : (strategies.find((s) => s.strategy_id === scope)?.name ?? scope);

  return (
    <section className="rv-principle-check" aria-label={t("pc.title")}>
      <h4>{t("pc.title")}</h4>
      {problem ? <p className="workspace-note">{problem}</p> : null}
      {check && check.version === null ? (
        <p className="workspace-note">{t("pc.none")}</p>
      ) : null}
      {check && check.version !== null ? (
        <>
          <p className="workspace-note">
            {t("pc.version", { version: check.version })}
            {check.after_the_fact ? ` · ${t("pc.afterTheFact")}` : ""}
          </p>
          <ul className="rv-principle-results">
            {check.results.map((result) => (
              <li key={result.item_id} className={`rv-status-${result.status}`}>
                <strong>{t(`pc.status.${result.status}`)}</strong>{" "}
                {t(`pr.kind.${result.kind}`)}
                {result.value !== null ? ` ${result.value}` : ""}
                {result.text ? ` — ${result.text}` : ""}
                <span className="workspace-note">
                  {" "}
                  ({scopeName(result.scope)}) {result.detail}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

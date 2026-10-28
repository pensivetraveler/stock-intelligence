import type {
  ReviewAccountCapitalList,
  ReviewSettings,
} from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { accountLabel } from "./account-label";
import { useI18n } from "./i18n";
import type { ReportModel, ReviewApi } from "./review-api";

/**
 * Report settings (ADR 0042, step 2a): the Claude model for reports, and each account's
 * capital from a date, which the report compares a position's size with.
 */
export function ReviewSettingsPanel({
  api,
  today,
}: {
  api: ReviewApi;
  today: string;
}) {
  const { t } = useI18n();
  const [settings, setSettings] = useState<ReviewSettings | null>(null);
  const [capital, setCapital] = useState<ReviewAccountCapitalList | null>(null);
  // An account is picked with its currency ("ref|USD"): one account can trade in both.
  const [form, setForm] = useState({
    account: "",
    effectiveFrom: today,
    amount: "",
  });
  const [problem, setProblem] = useState<string | null>(null);

  const fail = (error: unknown) =>
    setProblem(
      error instanceof Error ? error.message : t("common.requestFailed"),
    );

  useEffect(() => {
    let current = true;
    Promise.all([api.settings(), api.capital()])
      .then(([loadedSettings, loadedCapital]) => {
        if (!current) return;
        setSettings(loadedSettings);
        setCapital(loadedCapital);
        const first = loadedCapital.accounts[0];
        const key = first ? `${first.account_ref}|${first.currency}` : "";
        setForm((value) => ({ ...value, account: value.account || key }));
      })
      .catch((error: unknown) => current && fail(error));
    return () => {
      current = false;
    };
    // `fail` only formats the error; reloading on its identity would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);

  const [accountRef = "", currency = ""] = form.account.split("|");

  return (
    <section className="rv-settings" aria-label={t("rs.title")}>
      {problem ? (
        <p className="bt-problem" role="alert">
          {problem}
        </p>
      ) : null}
      <label className="bt-inline">
        {t("rs.model")}
        <select
          value={settings?.report_model ?? ""}
          disabled={!settings}
          onChange={(event) =>
            api
              .setReportModel(event.target.value as ReportModel)
              .then(setSettings)
              .catch(fail)
          }
        >
          {(settings?.models ?? []).map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </select>
      </label>
      <h4>{t("rs.capital")}</h4>
      <p className="workspace-note">{t("rs.capitalNote")}</p>
      {capital && capital.entries.length > 0 ? (
        <table className="bt-table" aria-label={t("rs.capital")}>
          <thead>
            <tr>
              <th>{t("rv.col.account")}</th>
              <th>{t("rs.from")}</th>
              <th>{t("rs.amount")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {capital.entries.map((entry) => (
              <tr key={entry.entry_id}>
                <td>{accountLabel(entry.account_ref, t)}</td>
                <td>{entry.effective_from}</td>
                <td>
                  {entry.amount} {entry.currency}
                </td>
                <td>
                  <button
                    type="button"
                    className="tb-button"
                    aria-label={t("rs.remove", { date: entry.effective_from })}
                    onClick={() =>
                      api
                        .removeCapital(entry.entry_id)
                        .then(() => api.capital())
                        .then(setCapital)
                        .catch(fail)
                    }
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {capital && capital.accounts.length > 0 ? (
        <form
          className="rv-capital-form"
          onSubmit={(event) => {
            event.preventDefault();
            api
              .addCapital({
                accountRef,
                currency: currency as "USD" | "KRW",
                effectiveFrom: form.effectiveFrom,
                amount: form.amount,
              })
              .then((loaded) => {
                setCapital(loaded);
                setForm({ ...form, amount: "" });
                setProblem(null);
              })
              .catch(fail);
          }}
        >
          <label className="bt-inline">
            {t("rv.col.account")}
            <select
              value={form.account}
              onChange={(event) =>
                setForm({ ...form, account: event.target.value })
              }
            >
              {capital.accounts.map((account) => {
                const key = `${account.account_ref}|${account.currency}`;
                return (
                  <option key={key} value={key}>
                    {accountLabel(account.account_ref, t)} · {account.currency}
                  </option>
                );
              })}
            </select>
          </label>
          <label className="bt-inline">
            {t("rs.from")}
            <input
              type="date"
              value={form.effectiveFrom}
              onChange={(event) =>
                setForm({ ...form, effectiveFrom: event.target.value })
              }
            />
          </label>
          <label className="bt-inline">
            {t("rs.amount")} ({currency})
            <input
              inputMode="decimal"
              value={form.amount}
              onChange={(event) =>
                setForm({ ...form, amount: event.target.value })
              }
            />
          </label>
          <button
            type="submit"
            className="tb-button"
            disabled={!accountRef || !form.amount}
          >
            {t("rs.add")}
          </button>
        </form>
      ) : (
        <p className="workspace-note">{t("rs.noAccounts")}</p>
      )}
    </section>
  );
}

import type {
  CredentialListResponse,
  CredentialSummary,
} from "@siw/contracts/types";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { exchangeTime } from "./chart-data";
import type { CredentialApi, KeyedProvider } from "./credential-api";
import { useI18n, type Translate } from "./i18n";
import { MarketApiError } from "./market-api";

type ProviderSpec = CredentialListResponse["providers"][number];

interface KeysPanelProps {
  api: CredentialApi;
}

function describe(t: Translate, error: unknown): string {
  if (error instanceof MarketApiError) {
    if (error.code === "credential_storage_unconfigured") {
      return t("keys.off");
    }
    if (error.code === "validation_error") {
      return t("keys.checkFields");
    }
  }
  return error instanceof Error ? error.message : t("common.requestFailed");
}

function emptySecrets(spec: ProviderSpec | undefined): Record<string, string> {
  return Object.fromEntries((spec?.fields ?? []).map((f) => [f.name, ""]));
}

/**
 * Register market-data keys. Broker APIs issue a key per brokerage account, so one
 * provider can hold several; they are used in order, and a key that hits the rate
 * limit is skipped while it cools down. Secret values go to the OS keychain and are
 * never displayed again: a key can only be replaced or deleted.
 */
export function KeysPanel({ api }: KeysPanelProps) {
  const { t } = useI18n();
  const [listing, setListing] = useState<CredentialListResponse | null>(null);
  // Kept raw and worded at render time, in the current language.
  const [error, setError] = useState<{ cause: unknown } | null>(null);
  const [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState<KeyedProvider>("kis");
  const [label, setLabel] = useState("");
  const [suffix, setSuffix] = useState("");
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [replacing, setReplacing] = useState<string | null>(null);
  const [replacement, setReplacement] = useState<Record<string, string>>({});
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .list()
      .then((result) => {
        setListing(result);
        setError(null);
      })
      .catch((reason: unknown) => setError({ cause: reason }));
  }, [api]);

  useEffect(() => refresh(), [refresh]);

  const specs = listing?.providers ?? [];
  const spec = specs.find((item) => item.provider === provider);
  const specOf = (name: string) => specs.find((item) => item.provider === name);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      setError(null);
      refresh();
      return true;
    } catch (reason) {
      setError({ cause: reason });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const values = { ...emptySecrets(spec), ...secrets };
    const added = await act(() =>
      api.add({
        provider,
        label,
        account_suffix: spec?.per_account && suffix ? suffix : null,
        secrets: values,
      }),
    );
    if (added) {
      setLabel("");
      setSuffix("");
      setSecrets({});
    }
  };

  const submitReplacement = async (
    event: FormEvent,
    item: CredentialSummary,
  ) => {
    event.preventDefault();
    const values = { ...emptySecrets(specOf(item.provider)), ...replacement };
    if (await act(() => api.replaceSecrets(item.credential_id, values))) {
      setReplacing(null);
      setReplacement({});
    }
  };

  return (
    <section className="import-panel keys-panel" aria-labelledby="keys-title">
      <h2 id="keys-title">{t("keys.title")}</h2>
      <p className="workspace-note">
        {t("keys.note", { provider: listing?.active_provider ?? "…" })}
      </p>
      {error ? (
        <p className="chart-error" role="alert">
          {describe(t, error.cause)}
        </p>
      ) : null}

      <form
        className="trade-fields"
        aria-label={t("keys.addForm")}
        onSubmit={(e) => void submit(e)}
      >
        <label>
          {t("keys.provider")}
          <select
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value as KeyedProvider);
              setSecrets({});
              setSuffix("");
            }}
          >
            {specs.map((item) => (
              <option key={item.provider} value={item.provider}>
                {item.display_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("keys.label")}
          <input
            value={label}
            maxLength={60}
            required
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        {spec?.per_account ? (
          <label>
            {t("keys.account")}
            <input
              value={suffix}
              inputMode="numeric"
              pattern="[0-9]{4}"
              maxLength={4}
              onChange={(event) => setSuffix(event.target.value)}
            />
          </label>
        ) : null}
        {(spec?.fields ?? []).map((field) => (
          <label key={field.name}>
            {field.label}
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              required
              value={secrets[field.name] ?? ""}
              onChange={(event) =>
                setSecrets({ ...secrets, [field.name]: event.target.value })
              }
            />
          </label>
        ))}
        <div className="trade-actions wide">
          <button type="submit" disabled={busy || !spec}>
            {t("keys.add")}
          </button>
        </div>
      </form>

      {specs.map((item) => {
        const keys = (listing?.credentials ?? []).filter(
          (key) => key.provider === item.provider,
        );
        if (keys.length === 0) return null;
        return (
          <div className="ledger" key={item.provider}>
            <h2>
              {item.display_name}
              {listing?.active_provider === item.provider
                ? t("keys.inUse")
                : ""}
            </h2>
            <table>
              <thead>
                <tr>
                  <th>{t("keys.order")}</th>
                  <th>{t("keys.label")}</th>
                  <th>{t("keys.accountColumn")}</th>
                  <th>{t("keys.enabled")}</th>
                  <th>{t("keys.status")}</th>
                  <th>{t("keys.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((key, index) => (
                  <tr key={key.credential_id}>
                    <td>{index + 1}</td>
                    <td>{key.label}</td>
                    <td>
                      {key.account_suffix ? `****${key.account_suffix}` : "—"}
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={t("keys.enable", { label: key.label })}
                        checked={key.enabled}
                        disabled={busy}
                        onChange={(event) =>
                          void act(() =>
                            api.update(key.credential_id, {
                              enabled: event.target.checked,
                            }),
                          )
                        }
                      />
                    </td>
                    <td className="import-notes">
                      {key.status ? (
                        <>
                          {t(`keyState.${key.status.state}`)}
                          {key.status.cooling_until_utc
                            ? t("keys.until", {
                                time: exchangeTime(
                                  key.status.cooling_until_utc,
                                ),
                              })
                            : ""}
                          {key.status.last_error
                            ? ` · ${key.status.last_error}`
                            : ""}
                        </>
                      ) : (
                        t("keys.notRunning")
                      )}
                    </td>
                    <td>
                      <div className="trade-actions">
                        <button
                          type="button"
                          className="secondary"
                          aria-label={t("keys.moveUp", { label: key.label })}
                          disabled={busy || index === 0}
                          onClick={() =>
                            void act(() => api.move(key.credential_id, "up"))
                          }
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          aria-label={t("keys.moveDown", { label: key.label })}
                          disabled={busy || index === keys.length - 1}
                          onClick={() =>
                            void act(() => api.move(key.credential_id, "down"))
                          }
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => {
                            setReplacing(key.credential_id);
                            setReplacement({});
                          }}
                        >
                          {t("keys.replace")}
                        </button>
                        {confirmDelete === key.credential_id ? (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => {
                              setConfirmDelete(null);
                              void act(() => api.remove(key.credential_id));
                            }}
                          >
                            {t("keys.confirmDelete")}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="secondary"
                            onClick={() => setConfirmDelete(key.credential_id)}
                          >
                            {t("keys.delete")}
                          </button>
                        )}
                      </div>
                      {replacing === key.credential_id ? (
                        <form
                          className="name-form"
                          aria-label={t("keys.replaceForm", {
                            label: key.label,
                          })}
                          onSubmit={(e) => void submitReplacement(e, key)}
                        >
                          {item.fields.map((field) => (
                            <label className="inline-field" key={field.name}>
                              {field.label}
                              <input
                                type="password"
                                autoComplete="off"
                                spellCheck={false}
                                required
                                value={replacement[field.name] ?? ""}
                                onChange={(event) =>
                                  setReplacement({
                                    ...replacement,
                                    [field.name]: event.target.value,
                                  })
                                }
                              />
                            </label>
                          ))}
                          <button type="submit" disabled={busy}>
                            {t("keys.saveSecret")}
                          </button>
                          <button
                            type="button"
                            className="secondary"
                            onClick={() => setReplacing(null)}
                          >
                            {t("keys.cancel")}
                          </button>
                        </form>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </section>
  );
}

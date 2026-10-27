import type { ReviewCheckStatus } from "@siw/contracts/types";
import { useEffect, useRef, useState } from "react";

import { useI18n } from "./i18n";
import type { ReviewApi } from "./review-api";

const POLL_MS = 3000;

/**
 * Background principle checks (ADR 0042, step 3b): progress, and a run over older dates.
 * Trades of the last 90 days are queued by the API on their own.
 */
export function ChecksBar({
  api,
  today,
  onProgress,
}: {
  api: ReviewApi;
  today: string;
  /** Called when checks finish, so the list can show the new results. */
  onProgress: () => void;
}) {
  const { t } = useI18n();
  const [status, setStatus] = useState<ReviewCheckStatus | null>(null);
  const [range, setRange] = useState({ from: "", to: today });
  const [problem, setProblem] = useState<string | null>(null);
  const busy = useRef(false);

  useEffect(() => {
    let current = true;
    const poll = () =>
      api
        .checkStatus()
        .then((loaded) => {
          if (!current) return;
          const working = loaded.queued > 0 || loaded.running;
          if (busy.current && !working) onProgress();
          busy.current = working;
          setStatus(loaded);
        })
        .catch(() => undefined);
    void poll();
    const timer = window.setInterval(poll, POLL_MS);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [api, onProgress]);

  const working = status !== null && (status.queued > 0 || status.running);
  return (
    <div className="rv-checks" aria-label={t("ck.title")}>
      <span className="workspace-note">
        {status
          ? working
            ? t("ck.working", { queued: status.queued, done: status.done })
            : t("ck.idle", { from: status.auto_from_date, done: status.done })
          : ""}
        {status && status.failed > 0
          ? ` · ${t("ck.failed", { n: status.failed })}`
          : ""}
      </span>
      <label className="bt-inline">
        {t("ck.from")}
        <input
          type="date"
          value={range.from}
          onChange={(event) => setRange({ ...range, from: event.target.value })}
        />
      </label>
      <label className="bt-inline">
        {t("ck.to")}
        <input
          type="date"
          value={range.to}
          onChange={(event) => setRange({ ...range, to: event.target.value })}
        />
      </label>
      <button
        type="button"
        className="tb-button"
        disabled={!range.from || !range.to}
        onClick={() => {
          setProblem(null);
          api
            .runChecks(range.from, range.to)
            .then((loaded) => {
              busy.current = true;
              setStatus(loaded);
            })
            .catch((error: unknown) =>
              setProblem(
                error instanceof Error ? error.message : String(error),
              ),
            );
        }}
      >
        {t("ck.run")}
      </button>
      {problem ? <span className="bt-problem">{problem}</span> : null}
    </div>
  );
}

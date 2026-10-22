import { tunablePaths, type WalkForwardDraft } from "./backtest-model";
import { useI18n, type MessageKey } from "./i18n";

const OBJECTIVES: WalkForwardDraft["objective"][] = [
  "net_return",
  "return_over_drawdown",
  "profit_factor",
  "win_rate",
];

/**
 * Walk-forward settings (ADR 0032): rolling in-sample / out-of-sample windows in bars,
 * the objective and up to three numbers of the rules strategy to try.
 */
export function WalkForwardFields({
  draft,
  definition,
  onChange,
}: {
  draft: WalkForwardDraft;
  /** The rules definition being edited, for the numbers that can be varied. */
  definition: unknown;
  onChange: (draft: WalkForwardDraft) => void;
}) {
  const { t } = useI18n();
  const paths = tunablePaths(definition);
  return (
    <fieldset className="bt-walk-forward">
      <legend>
        <label>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) =>
              onChange({ ...draft, enabled: event.target.checked })
            }
          />{" "}
          {t("bt.wf.title")}
        </label>
      </legend>
      {draft.enabled ? (
        <>
          <p className="workspace-note">{t("bt.wf.note")}</p>
          <div className="bt-grid">
            <label>
              {t("bt.wf.inSample")}
              <input
                inputMode="numeric"
                value={draft.inSampleBars}
                onChange={(event) =>
                  onChange({ ...draft, inSampleBars: event.target.value })
                }
              />
            </label>
            <label>
              {t("bt.wf.outOfSample")}
              <input
                inputMode="numeric"
                value={draft.outOfSampleBars}
                onChange={(event) =>
                  onChange({ ...draft, outOfSampleBars: event.target.value })
                }
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={draft.anchored ?? false}
                onChange={(event) =>
                  onChange({ ...draft, anchored: event.target.checked })
                }
              />{" "}
              {t("bt.wf.anchored")}
            </label>
            <label>
              {t("bt.wf.objective")}
              <select
                value={draft.objective}
                onChange={(event) =>
                  onChange({
                    ...draft,
                    objective: event.target
                      .value as WalkForwardDraft["objective"],
                  })
                }
              >
                {OBJECTIVES.map((objective) => (
                  <option key={objective} value={objective}>
                    {t(`bt.wf.objective.${objective}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {draft.grid.map((row, index) => (
            <div className="bt-grid" key={index}>
              <label>
                {t("bt.wf.parameter", { n: index + 1 })}
                <select
                  value={row.path}
                  onChange={(event) => {
                    const grid = [...draft.grid];
                    grid[index] = { ...row, path: event.target.value };
                    onChange({ ...draft, grid });
                  }}
                >
                  <option value="">—</option>
                  {paths.map((item) => (
                    <option key={item.path} value={item.path}>
                      {item.label} ({item.current})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("bt.wf.values")}
                <input
                  value={row.values}
                  placeholder="10, 20, 30"
                  onChange={(event) => {
                    const grid = [...draft.grid];
                    grid[index] = { ...row, values: event.target.value };
                    onChange({ ...draft, grid });
                  }}
                />
              </label>
            </div>
          ))}
          {draft.grid.length < 3 ? (
            <button
              type="button"
              className="tb-button"
              onClick={() =>
                onChange({
                  ...draft,
                  grid: [...draft.grid, { path: "", values: "" }],
                })
              }
            >
              {t("bt.wf.addParameter")}
            </button>
          ) : null}
        </>
      ) : null}
    </fieldset>
  );
}

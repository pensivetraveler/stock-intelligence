import { useState, type ReactNode } from "react";

import { useI18n } from "./i18n";
import {
  DEFAULT_LEVEL_COLOR,
  LINE_STYLES,
  levelsOf,
  MAX_LINE_WIDTH,
  MAX_PRECISION,
  PRICE_SOURCES,
  VISIBILITY_GROUPS,
  type IndicatorDefinition,
  type IndicatorInstance,
  type IndicatorStyle,
  type OutputStyle,
  type PriceSource,
  type VisibilityGroup,
  type VisibilityRange,
} from "./indicators";

type Tab = "inputs" | "style" | "visibility";

/**
 * An indicator's settings, laid out like TradingView's: "Inputs" (lengths, source,
 * timeframe) and "Style" (each line's visibility, colour, width and line style, and
 * the guide levels). Every change applies at once; "Done" only closes.
 */
export function IndicatorSettings({
  definition,
  instance,
  inputs,
  onChange,
  onClose,
}: {
  definition: IndicatorDefinition;
  instance: IndicatorInstance;
  /** The Inputs tab's own fields (lengths or a formula), and the timeframe field. */
  inputs: ReactNode;
  onChange: (update: (item: IndicatorInstance) => IndicatorInstance) => void;
  onClose: () => void;
}) {
  const { t, language } = useI18n();
  const [tab, setTab] = useState<Tab>("inputs");
  const setStyle = (update: (style: IndicatorStyle) => IndicatorStyle) =>
    onChange((item) => {
      const next = update(item.style ?? {});
      const copy = { ...item };
      if (Object.keys(next).length > 0) copy.style = next;
      else delete copy.style;
      return copy;
    });
  const setOutput = (key: string, change: OutputStyle) =>
    setStyle((style) => ({
      ...style,
      outputs: {
        ...style.outputs,
        [key]: { ...style.outputs?.[key], ...change },
      },
    }));
  const levels = levelsOf(definition, instance);
  const setLevels = (next: typeof levels) =>
    setStyle((style) => ({ ...style, levels: next }));

  return (
    <div
      className="popover indicator-settings"
      role="dialog"
      aria-label={t("ind.settings", { name: definition.short })}
    >
      <strong>
        {instance.type === "formula"
          ? (instance.label ?? definition.short)
          : definition.name[language]}
      </strong>
      <div className="ind-tabs" role="tablist">
        {(["inputs", "style", "visibility"] as const).map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={tab === item}
            className={tab === item ? "ind-tab active" : "ind-tab"}
            onClick={() => setTab(item)}
          >
            {t(`ind.tab.${item}`)}
          </button>
        ))}
      </div>
      {tab === "inputs" ? (
        <div className="ind-tab-body">
          {definition.sourceInput ? (
            <label>
              {t("ind.source")}
              <select
                value={instance.source ?? "close"}
                onChange={(event) =>
                  onChange((item) => {
                    const value = event.target.value as PriceSource;
                    const copy = { ...item };
                    if (value === "close") delete copy.source;
                    else copy.source = value;
                    return copy;
                  })
                }
              >
                {PRICE_SOURCES.map((source) => (
                  <option key={source} value={source}>
                    {t(`ind.source.${source}`)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {inputs}
        </div>
      ) : tab === "style" ? (
        <div className="ind-tab-body">
          {definition.outputs.map((output) => {
            const look = instance.style?.outputs?.[output.key] ?? {};
            return (
              <div key={output.key} className="ind-style-row">
                <input
                  type="checkbox"
                  checked={look.visible ?? true}
                  aria-label={t("ind.style.visible", { name: output.label })}
                  onChange={(event) =>
                    setOutput(output.key, { visible: event.target.checked })
                  }
                />
                <span className="ind-style-name">{output.label}</span>
                <input
                  type="color"
                  value={look.color ?? output.color}
                  aria-label={t("ind.style.color", { name: output.label })}
                  onChange={(event) =>
                    setOutput(output.key, { color: event.target.value })
                  }
                />
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={look.opacity ?? 100}
                  className="ind-opacity"
                  title={`${look.opacity ?? 100}%`}
                  aria-label={t("ind.style.opacity", { name: output.label })}
                  onChange={(event) =>
                    setOutput(output.key, {
                      opacity: Number(event.target.value),
                    })
                  }
                />
                {(output.kind ?? "line") === "line" ? (
                  <>
                    <select
                      value={look.width ?? 1}
                      aria-label={t("ind.style.width", { name: output.label })}
                      onChange={(event) =>
                        setOutput(output.key, {
                          width: Number(event.target.value),
                        })
                      }
                    >
                      {Array.from(
                        { length: MAX_LINE_WIDTH },
                        (_, i) => i + 1,
                      ).map((width) => (
                        <option key={width} value={width}>
                          {width}px
                        </option>
                      ))}
                    </select>
                    <select
                      value={look.line ?? "solid"}
                      aria-label={t("ind.style.line", { name: output.label })}
                      onChange={(event) =>
                        setOutput(output.key, {
                          line: event.target
                            .value as (typeof LINE_STYLES)[number],
                        })
                      }
                    >
                      {LINE_STYLES.map((line) => (
                        <option key={line} value={line}>
                          {t(`ind.line.${line}`)}
                        </option>
                      ))}
                    </select>
                  </>
                ) : null}
              </div>
            );
          })}
          {definition.pane === "separate" ? (
            <fieldset className="ind-levels">
              <legend>{t("ind.style.levels")}</legend>
              {levels.map((level, index) => (
                <div key={index} className="ind-style-row">
                  <input
                    type="checkbox"
                    checked={level.visible}
                    aria-label={t("ind.style.levelVisible", {
                      value: level.value,
                    })}
                    onChange={(event) =>
                      setLevels(
                        levels.map((item, i) =>
                          i === index
                            ? { ...item, visible: event.target.checked }
                            : item,
                        ),
                      )
                    }
                  />
                  <input
                    type="number"
                    value={level.value}
                    aria-label={t("ind.style.levelValue")}
                    onChange={(event) => {
                      const value = Number(event.target.value);
                      if (!Number.isFinite(value)) return;
                      setLevels(
                        levels.map((item, i) =>
                          i === index ? { ...item, value } : item,
                        ),
                      );
                    }}
                  />
                  <input
                    type="color"
                    value={level.color}
                    aria-label={t("ind.style.levelColor", {
                      value: level.value,
                    })}
                    onChange={(event) =>
                      setLevels(
                        levels.map((item, i) =>
                          i === index
                            ? { ...item, color: event.target.value }
                            : item,
                        ),
                      )
                    }
                  />
                  <button
                    type="button"
                    aria-label={t("ind.style.removeLevel", {
                      value: level.value,
                    })}
                    onClick={() =>
                      setLevels(levels.filter((_, i) => i !== index))
                    }
                  >
                    ✕
                  </button>
                </div>
              ))}
              {levels.length < 10 ? (
                <button
                  type="button"
                  className="tb-button"
                  onClick={() =>
                    setLevels([
                      ...levels,
                      { value: 0, color: DEFAULT_LEVEL_COLOR, visible: true },
                    ])
                  }
                >
                  {t("ind.style.addLevel")}
                </button>
              ) : null}
            </fieldset>
          ) : null}
          <label>
            {t("ind.style.precision")}
            <select
              value={instance.style?.precision ?? ""}
              onChange={(event) =>
                setStyle((style) => {
                  const next = { ...style };
                  if (event.target.value === "") delete next.precision;
                  else next.precision = Number(event.target.value);
                  return next;
                })
              }
            >
              <option value="">{t("ind.style.precisionAuto")}</option>
              {Array.from({ length: MAX_PRECISION + 1 }, (_, i) => i).map(
                (digits) => (
                  <option key={digits} value={digits}>
                    {digits === 0 ? "1" : `0.${"0".repeat(digits - 1)}1`}
                  </option>
                ),
              )}
            </select>
          </label>
          <button
            type="button"
            className="tb-button"
            onClick={() => setStyle(() => ({}))}
          >
            {t("ind.style.reset")}
          </button>
        </div>
      ) : (
        <VisibilityTab
          instance={instance}
          onChange={(group, range) =>
            onChange((item) => {
              const visibility = { ...item.visibility };
              if (range === undefined) delete visibility[group];
              else visibility[group] = range;
              const copy = { ...item };
              if (Object.keys(visibility).length > 0) {
                copy.visibility = visibility;
              } else {
                delete copy.visibility;
              }
              return copy;
            })
          }
        />
      )}
      <button type="button" className="tb-button" onClick={onClose}>
        {t("ind.done")}
      </button>
    </div>
  );
}

/** TradingView's "Visibility": per group of chart timeframes, on or off and a range. */
function VisibilityTab({
  instance,
  onChange,
}: {
  instance: IndicatorInstance;
  /** `undefined` goes back to "all timeframes of this group". */
  onChange: (
    group: VisibilityGroup,
    range: VisibilityRange | undefined,
  ) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="ind-tab-body">
      {(Object.keys(VISIBILITY_GROUPS) as VisibilityGroup[]).map((group) => {
        const limits = VISIBILITY_GROUPS[group];
        const saved = instance.visibility?.[group];
        const on = saved !== false;
        const range =
          saved === undefined || saved === false
            ? { from: limits.min, to: limits.max }
            : saved;
        const setRange = (from: number, to: number) => {
          const clamp = (n: number) =>
            Math.min(limits.max, Math.max(limits.min, Math.round(n)));
          const next = { from: clamp(from), to: clamp(to) };
          if (next.from > next.to) return;
          onChange(
            group,
            next.from === limits.min && next.to === limits.max
              ? undefined
              : next,
          );
        };
        return (
          <div key={group} className="ind-style-row">
            <input
              type="checkbox"
              checked={on}
              aria-label={t(`ind.vis.${group}`)}
              onChange={(event) =>
                onChange(group, event.target.checked ? undefined : false)
              }
            />
            <span className="ind-style-name">{t(`ind.vis.${group}`)}</span>
            <input
              type="number"
              min={limits.min}
              max={limits.max}
              value={range.from}
              disabled={!on}
              aria-label={t("ind.vis.from", { group: t(`ind.vis.${group}`) })}
              onChange={(event) =>
                setRange(Number(event.target.value), range.to)
              }
            />
            <span>–</span>
            <input
              type="number"
              min={limits.min}
              max={limits.max}
              value={range.to}
              disabled={!on}
              aria-label={t("ind.vis.to", { group: t(`ind.vis.${group}`) })}
              onChange={(event) =>
                setRange(range.from, Number(event.target.value))
              }
            />
          </div>
        );
      })}
    </div>
  );
}

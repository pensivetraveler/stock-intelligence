import { useState, type ReactNode } from "react";

import { useI18n } from "./i18n";
import {
  DEFAULT_LEVEL_COLOR,
  LINE_STYLES,
  levelsOf,
  MAX_LINE_WIDTH,
  PRICE_SOURCES,
  type IndicatorDefinition,
  type IndicatorInstance,
  type IndicatorStyle,
  type OutputStyle,
  type PriceSource,
} from "./indicators";

type Tab = "inputs" | "style";

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
        {(["inputs", "style"] as const).map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={tab === item}
            className={tab === item ? "ind-tab active" : "ind-tab"}
            onClick={() => setTab(item)}
          >
            {t(item === "inputs" ? "ind.tab.inputs" : "ind.tab.style")}
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
      ) : (
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
          <button
            type="button"
            className="tb-button"
            onClick={() => setStyle(() => ({}))}
          >
            {t("ind.style.reset")}
          </button>
        </div>
      )}
      <button type="button" className="tb-button" onClick={onClose}>
        {t("ind.done")}
      </button>
    </div>
  );
}

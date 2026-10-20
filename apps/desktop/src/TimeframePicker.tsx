import { useState, type FormEvent } from "react";

import { useI18n } from "./i18n";
import { ChevronDownIcon } from "./icons";
import {
  customTimeframe,
  MAX_TIMEFRAME_MINUTES,
  normalizeFavorites,
  PRESET_TIMEFRAMES,
  timeframeLabel,
} from "./timeframes";

interface TimeframePickerProps {
  value: string;
  favorites: string[];
  onChange: (timeframe: string) => void;
  onToggleFavorite: (timeframe: string) => void;
}

/**
 * Favorite timeframes as buttons, plus a menu with the presets (star to favorite) and
 * a custom minutes/hours entry.
 */
export function TimeframePicker({
  value,
  favorites,
  onChange,
  onToggleFavorite,
}: TimeframePickerProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [unit, setUnit] = useState<"minutes" | "hours">("minutes");
  const [error, setError] = useState(false);
  const shown = favorites.includes(value) ? favorites : [...favorites, value];
  const listed = normalizeFavorites([...PRESET_TIMEFRAMES, ...favorites]);

  const apply = (event: FormEvent) => {
    event.preventDefault();
    const timeframe = customTimeframe(Number(amount), unit);
    if (timeframe === null) {
      setError(true);
      return;
    }
    setError(false);
    setAmount("");
    onChange(timeframe);
    setOpen(false);
  };

  return (
    <div className="timeframe-picker">
      <div className="tb-group" role="group" aria-label={t("chart.timeframe")}>
        {shown.map((item) => (
          <button
            key={item}
            type="button"
            className="tb-button"
            aria-pressed={item === value}
            onClick={() => onChange(item)}
          >
            {timeframeLabel(item)}
          </button>
        ))}
      </div>
      <button
        type="button"
        className="tb-button"
        aria-label={t("tf.more")}
        title={t("tf.more")}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <ChevronDownIcon />
      </button>
      {open ? (
        <div
          className="popover timeframe-popover"
          role="dialog"
          aria-label={t("tf.menu")}
        >
          <ul>
            {listed.map((item) => {
              const favorite = favorites.includes(item);
              return (
                <li key={item}>
                  <button
                    type="button"
                    className="timeframe-option"
                    aria-pressed={item === value}
                    onClick={() => {
                      onChange(item);
                      setOpen(false);
                    }}
                  >
                    {timeframeLabel(item)}
                  </button>
                  <button
                    type="button"
                    className={favorite ? "star on" : "star"}
                    aria-pressed={favorite}
                    aria-label={t(favorite ? "tf.unfavorite" : "tf.favorite", {
                      timeframe: timeframeLabel(item),
                    })}
                    onClick={() => onToggleFavorite(item)}
                  >
                    {favorite ? "★" : "☆"}
                  </button>
                </li>
              );
            })}
          </ul>
          <form className="timeframe-custom" onSubmit={apply}>
            <label>
              {t("tf.custom")}
              <input
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={amount}
                aria-invalid={error}
                onChange={(event) => {
                  setAmount(event.target.value);
                  setError(false);
                }}
              />
            </label>
            <select
              aria-label={t("tf.unit")}
              value={unit}
              onChange={(event) =>
                setUnit(event.target.value as "minutes" | "hours")
              }
            >
              <option value="minutes">{t("tf.minutes")}</option>
              <option value="hours">{t("tf.hours")}</option>
            </select>
            <button type="submit" className="tb-button">
              {t("tf.apply")}
            </button>
            {error ? (
              <span className="chart-error" role="alert">
                {t("tf.invalid", { max: MAX_TIMEFRAME_MINUTES / 60 })}
              </span>
            ) : null}
          </form>
        </div>
      ) : null}
    </div>
  );
}

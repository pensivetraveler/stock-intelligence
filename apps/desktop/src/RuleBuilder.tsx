import {
  backtestIndicator,
  backtestOutputs,
  COMPARISONS,
  conditionKey,
  indicatorLabel,
  indicatorOperand,
  MARKET_ORDER,
  PRICES,
  ruleIndicatorIds,
  SIZING_MODES,
  timeCondition,
  type ConditionDraft,
  type OperandDraft,
  type OrderDraft,
  type OrderKind,
  type RulesDraft,
  type TrailingMode,
} from "./backtest-model";
import { useI18n, type MessageKey } from "./i18n";

function OperandEditor({
  operand,
  onChange,
  label,
}: {
  operand: OperandDraft;
  onChange: (next: OperandDraft) => void;
  label: string;
}) {
  const { t, language } = useI18n();
  return (
    <span className="bt-operand" role="group" aria-label={label}>
      <select
        aria-label={t("bt.operandKind", { side: label })}
        value={operand.kind}
        onChange={(event) => {
          const kind = event.target.value;
          onChange(
            kind === "indicator"
              ? indicatorOperand("sma")
              : kind === "price"
                ? { kind: "price", price: "close" }
                : { kind: "value", value: "0" },
          );
        }}
      >
        <option value="indicator">{t("bt.kind.indicator")}</option>
        <option value="price">{t("bt.kind.price")}</option>
        <option value="value">{t("bt.kind.value")}</option>
      </select>
      {operand.kind === "indicator" ? (
        <>
          <select
            aria-label={t("bt.indicator", { side: label })}
            value={operand.indicator}
            onChange={(event) =>
              onChange({
                ...indicatorOperand(event.target.value),
                ...(operand.offset ? { offset: operand.offset } : {}),
              } as OperandDraft)
            }
          >
            {ruleIndicatorIds(operand.indicator).map((id) => (
              <option key={id} value={id}>
                {indicatorLabel(id, language)}
              </option>
            ))}
          </select>
          {backtestIndicator(operand.indicator).inputs.map((input) => (
            <input
              key={input.key}
              type="number"
              className="bt-param"
              aria-label={`${label} ${input.label[language]}`}
              title={input.label[language]}
              min={input.min}
              max={input.max}
              step={input.step ?? 1}
              value={operand.params[input.key]}
              onChange={(event) =>
                onChange({
                  ...operand,
                  params: {
                    ...operand.params,
                    [input.key]: Number(event.target.value),
                  },
                })
              }
            />
          ))}
          {backtestOutputs(operand.indicator).length > 1 ? (
            <select
              aria-label={t("bt.output", { side: label })}
              value={operand.output}
              onChange={(event) =>
                onChange({ ...operand, output: event.target.value })
              }
            >
              {backtestOutputs(operand.indicator).map((output) => (
                <option key={output.key} value={output.key}>
                  {output.label}
                </option>
              ))}
            </select>
          ) : null}
        </>
      ) : null}
      {operand.kind === "price" ? (
        <select
          aria-label={t("bt.priceField", { side: label })}
          value={operand.price}
          onChange={(event) =>
            onChange({
              ...operand,
              price: event.target.value as (typeof PRICES)[number],
            })
          }
        >
          {PRICES.map((price) => (
            <option key={price} value={price}>
              {t(`bt.price.${price}` as MessageKey)}
            </option>
          ))}
        </select>
      ) : null}
      {operand.kind === "value" ? (
        <input
          className="bt-param"
          inputMode="decimal"
          aria-label={t("bt.valueInput", { side: label })}
          value={operand.value}
          onChange={(event) =>
            onChange({ kind: "value", value: event.target.value })
          }
        />
      ) : null}
      {operand.kind === "value" ? null : (
        <input
          type="number"
          className="bt-param"
          aria-label={t("bt.barsBack", { side: label })}
          title={t("bt.barsBackHint")}
          min={0}
          max={500}
          placeholder={t("bt.barsBackShort")}
          value={operand.offset ?? ""}
          onChange={(event) =>
            onChange({
              ...operand,
              offset: Number(event.target.value) || undefined,
            })
          }
        />
      )}
    </span>
  );
}

/** A bar's start in the exchange's local time, from inclusive and to exclusive. */
function TimeFields({
  n,
  time,
  onChange,
}: {
  n: number;
  time: { from: string; to: string };
  onChange: (time: { from: string; to: string }) => void;
}) {
  const { t } = useI18n();
  return (
    <span className="bt-operand" role="group" title={t("bt.timeHint")}>
      {t("bt.timeCondition")}
      <input
        type="time"
        aria-label={t("bt.timeFrom", { n })}
        value={time.from}
        onChange={(event) => onChange({ ...time, from: event.target.value })}
      />
      <input
        type="time"
        aria-label={t("bt.timeTo", { n })}
        value={time.to}
        onChange={(event) => onChange({ ...time, to: event.target.value })}
      />
    </span>
  );
}

function ConditionList({
  title,
  mode,
  conditions,
  onChange,
}: {
  title: string;
  mode: "all" | "any";
  conditions: ConditionDraft[];
  onChange: (mode: "all" | "any", conditions: ConditionDraft[]) => void;
}) {
  const { t } = useI18n();
  const replace = (index: number, next: ConditionDraft) =>
    onChange(
      mode,
      conditions.map((item, i) => (i === index ? next : item)),
    );
  return (
    <fieldset className="bt-conditions">
      <legend>{title}</legend>
      <label className="bt-inline">
        {t("bt.match")}{" "}
        <select
          aria-label={t("bt.matchFor", { title })}
          value={mode}
          onChange={(event) =>
            onChange(event.target.value as "all" | "any", conditions)
          }
        >
          <option value="all">{t("bt.match.all")}</option>
          <option value="any">{t("bt.match.any")}</option>
        </select>
      </label>
      {conditions.map((condition, index) => (
        <div key={condition.key} className="bt-condition">
          {condition.time ? (
            <TimeFields
              n={index + 1}
              time={condition.time}
              onChange={(time) => replace(index, { ...condition, time })}
            />
          ) : (
            <>
              <OperandEditor
                label={t("bt.left", { n: index + 1 })}
                operand={condition.left}
                onChange={(left) => replace(index, { ...condition, left })}
              />
              <select
                aria-label={t("bt.comparison", { n: index + 1 })}
                value={condition.op}
                onChange={(event) =>
                  replace(index, {
                    ...condition,
                    op: event.target.value as ConditionDraft["op"],
                  })
                }
              >
                {COMPARISONS.map((op) => (
                  <option key={op} value={op}>
                    {t(`bt.op.${op}` as MessageKey)}
                  </option>
                ))}
              </select>
              <OperandEditor
                label={t("bt.right", { n: index + 1 })}
                operand={condition.right}
                onChange={(right) => replace(index, { ...condition, right })}
              />
            </>
          )}
          <button
            type="button"
            className="tb-button"
            aria-label={t("bt.removeCondition", { n: index + 1 })}
            onClick={() =>
              onChange(
                mode,
                conditions.filter((_, i) => i !== index),
              )
            }
          >
            ✕
          </button>
        </div>
      ))}
      <button
        type="button"
        className="tb-button"
        onClick={() =>
          onChange(mode, [
            ...conditions,
            {
              key: conditionKey(),
              left: { kind: "price", price: "close" },
              op: "gt",
              right: indicatorOperand("sma"),
            },
          ])
        }
      >
        {t("bt.addCondition")}
      </button>
      <button
        type="button"
        className="tb-button"
        onClick={() => onChange(mode, [...conditions, timeCondition()])}
      >
        {t("bt.addTimeCondition")}
      </button>
    </fieldset>
  );
}

/** Form for a rules strategy; the API decides whether the result is valid. */
/** Market, or a limit/stop order at an offset from the signal close with an expiry. */
function OrderFields({
  title,
  order,
  onChange,
}: {
  title: string;
  order: OrderDraft;
  onChange: (order: OrderDraft) => void;
}) {
  const { t } = useI18n();
  return (
    <>
      <label>
        {title}
        <select
          value={order.type}
          onChange={(event) =>
            onChange({ ...order, type: event.target.value as OrderKind })
          }
        >
          {(["market", "limit", "stop"] as OrderKind[]).map((kind) => (
            <option key={kind} value={kind}>
              {t(`bt.order.${kind}` as MessageKey)}
            </option>
          ))}
        </select>
      </label>
      {order.type === "market" ? null : (
        <>
          <label>
            {t("bt.orderOffset", { title })}
            <input
              inputMode="decimal"
              value={order.offsetPct}
              placeholder={order.type === "limit" ? "-1" : "1"}
              onChange={(event) =>
                onChange({ ...order, offsetPct: event.target.value })
              }
            />
          </label>
          <label>
            {t("bt.orderExpiry", { title })}
            <input
              inputMode="numeric"
              value={order.expireBars}
              onChange={(event) =>
                onChange({ ...order, expireBars: event.target.value })
              }
            />
          </label>
        </>
      )}
    </>
  );
}

export function RuleBuilder({
  draft,
  onChange,
}: {
  draft: RulesDraft;
  onChange: (next: RulesDraft) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="bt-rules">
      <ConditionList
        title={t("bt.entry")}
        mode={draft.entryMode}
        conditions={draft.entry}
        onChange={(entryMode, entry) =>
          onChange({ ...draft, entryMode, entry })
        }
      />
      <ConditionList
        title={t("bt.exit")}
        mode={draft.exitMode}
        conditions={draft.exit}
        onChange={(exitMode, exit) => onChange({ ...draft, exitMode, exit })}
      />
      <div className="bt-grid">
        <OrderFields
          title={t("bt.entryOrder")}
          order={draft.entryOrder ?? MARKET_ORDER}
          onChange={(entryOrder) => onChange({ ...draft, entryOrder })}
        />
        <OrderFields
          title={t("bt.exitOrder")}
          order={draft.exitOrder ?? MARKET_ORDER}
          onChange={(exitOrder) => onChange({ ...draft, exitOrder })}
        />
      </div>
      <div className="bt-grid">
        <label>
          {t("bt.stopLoss")}
          <input
            inputMode="decimal"
            value={draft.stopLossPct}
            placeholder="—"
            onChange={(event) =>
              onChange({ ...draft, stopLossPct: event.target.value })
            }
          />
        </label>
        <label>
          {t("bt.takeProfit")}
          <input
            inputMode="decimal"
            value={draft.takeProfitPct}
            placeholder="—"
            onChange={(event) =>
              onChange({ ...draft, takeProfitPct: event.target.value })
            }
          />
        </label>
        <label>
          {t("bt.stopAtrLength")}
          <input
            inputMode="numeric"
            value={draft.stopAtrLength ?? "14"}
            onChange={(event) =>
              onChange({ ...draft, stopAtrLength: event.target.value })
            }
          />
        </label>
        <label>
          {t("bt.stopAtrMult")}
          <input
            inputMode="decimal"
            value={draft.stopAtrMult ?? ""}
            placeholder="—"
            onChange={(event) =>
              onChange({ ...draft, stopAtrMult: event.target.value })
            }
          />
        </label>
        <label>
          {t("bt.trailing")}
          <select
            value={draft.trailingMode ?? "none"}
            onChange={(event) =>
              onChange({
                ...draft,
                trailingMode: event.target.value as TrailingMode,
              })
            }
          >
            {(["none", "pct", "atr"] as TrailingMode[]).map((mode) => (
              <option key={mode} value={mode}>
                {t(`bt.trailing.${mode}` as MessageKey)}
              </option>
            ))}
          </select>
        </label>
        {draft.trailingMode === "pct" ? (
          <label>
            {t("bt.trailingValue")}
            <input
              inputMode="decimal"
              value={draft.trailingValue ?? ""}
              onChange={(event) =>
                onChange({ ...draft, trailingValue: event.target.value })
              }
            />
          </label>
        ) : null}
        {draft.trailingMode === "atr" ? (
          <>
            <label>
              {t("bt.trailingLength")}
              <input
                inputMode="numeric"
                value={draft.trailingLength ?? "14"}
                onChange={(event) =>
                  onChange({ ...draft, trailingLength: event.target.value })
                }
              />
            </label>
            <label>
              {t("bt.trailingMult")}
              <input
                inputMode="decimal"
                value={draft.trailingMult ?? ""}
                onChange={(event) =>
                  onChange({ ...draft, trailingMult: event.target.value })
                }
              />
            </label>
          </>
        ) : null}
        <label>
          {t("bt.sizing")}
          <select
            value={draft.sizingMode}
            onChange={(event) =>
              onChange({
                ...draft,
                sizingMode: event.target.value as RulesDraft["sizingMode"],
              })
            }
          >
            {SIZING_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {t(`bt.sizing.${mode}` as MessageKey)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("bt.sizingValue")}
          <input
            inputMode="decimal"
            value={draft.sizingValue}
            onChange={(event) =>
              onChange({ ...draft, sizingValue: event.target.value })
            }
          />
        </label>
        <label>
          {t("bt.maxPositions")}
          <input
            inputMode="numeric"
            value={draft.maxPositions}
            onChange={(event) =>
              onChange({ ...draft, maxPositions: event.target.value })
            }
          />
        </label>
      </div>
    </div>
  );
}

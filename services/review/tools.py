"""Read-only data tools for follow-up questions on a trade report (ADR 0042, step 2b).

Every tool reads market data or saved strategies; none writes anything. Data stops at the
trade's exit (or now, for an open trade) unless Claude asks for later data, and then each
later bar is flagged hindsight. Chart images are not drawn here: the tool returns the
bars cut at their time and the app draws them, like the report's images.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from services.backtest.data import GetBars
from services.backtest.indicators import INDICATORS, Bar, compute
from services.backtest.strategy import RuleEvaluator, StrategyError, validate_definition
from services.market_data.calendar import CalendarError, SessionWindow
from services.market_data.instruments import calendar_for
from services.market_data.provider import BarsQuery, MarketDataError
from services.review.report import QQQ, SPY, executions_of
from services.review.trades import Trade

TIMEFRAMES = ("5m", "15m", "60m", "1d", "1w")
MAX_SESSIONS = {"5m": 5, "15m": 10, "60m": 20}
MAX_DAYS = {"1d": 400, "1w": 2200}
TABLE_BARS = 100
IMAGE_BARS = 400
SIGNAL_BARS = 5

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_bars",
        "description": (
            "OHLCV bars of a symbol for a date range, with a summary. 'this' is the traded "
            "stock. By default nothing after the trade's exit is returned; set "
            "include_after_exit to see later bars, each then flagged hindsight. At most "
            f"{TABLE_BARS} bars are listed (the latest); the summary covers all of them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "'this', or a ticker like SPY"},
                "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
                "start_date": {"type": "string", "description": "YYYY-MM-DD, exchange date"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, exchange date"},
                "include_after_exit": {"type": "boolean"},
            },
            "required": ["symbol", "timeframe", "start_date", "end_date"],
        },
    },
    {
        "name": "get_indicator",
        "description": (
            "An indicator's value(s) on the last bar that had closed by the trade's first "
            "buy ('entry') or last sell ('exit'). Parameters use the app's names, e.g. "
            '{"length": 20} for sma, ema, rsi or atr.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
                "indicator": {"type": "string", "enum": sorted(INDICATORS)},
                "params": {"type": "object"},
                "at": {"type": "string", "enum": ["entry", "exit"]},
            },
            "required": ["symbol", "timeframe", "indicator", "at"],
        },
    },
    {
        "name": "chart_image",
        "description": (
            "A candlestick chart image of a symbol for a date range, with this trade's buys "
            "and sells marked when the symbol is the traded stock. Same cut as get_bars. "
            f"At most {IMAGE_BARS} bars (the latest) are drawn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "include_after_exit": {"type": "boolean"},
            },
            "required": ["symbol", "timeframe", "start_date", "end_date"],
        },
    },
    {
        "name": "list_strategies",
        "description": (
            "The investor's saved strategies: id, name, type, whether this trade is linked "
            "to it, and the rule definition of rule strategies."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "strategy_signals",
        "description": (
            "Whether a saved rule strategy's entry and exit rules held on the last bars that "
            "had closed by the trade's first buy ('entry') or last sell ('exit'), on the "
            "traded stock. Without strategy_id, the strategy linked to this trade is used."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string"},
                "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
                "at": {"type": "string", "enum": ["entry", "exit"]},
            },
            "required": ["timeframe", "at"],
        },
    },
]
IMAGE_TOOLS = {"chart_image"}


class ToolInputError(ValueError):
    """The tool input cannot be served; the message goes back to Claude."""


@dataclass(frozen=True)
class ToolResult:
    """Data for Claude (`data`), or bars the app must draw first (`image`)."""

    summary: str  # one line for the conversation view
    data: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    hindsight: bool = False


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _date(value: object, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ToolInputError(f"{name} must be YYYY-MM-DD") from exc


def _timeframe(value: object) -> str:
    if value not in TIMEFRAMES:
        raise ToolInputError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
    return str(value)


@dataclass
class ToolContext:
    get_bars: GetBars
    trade: Trade
    now: datetime
    resolve_symbol: Callable[[str], str | None]
    strategies: Callable[[], list[dict[str, Any]]]

    def __post_init__(self) -> None:
        executions = executions_of(self.trade)
        self.executions = executions
        buys = [e for e in executions if e.side == "buy"]
        sells = [e for e in executions if e.side == "sell"]
        zone = calendar_for(self.trade.instrument_id).zone

        def start_of(day: date) -> datetime:
            return datetime.combine(day, datetime.min.time(), zone).astimezone(timezone.utc)

        first = buys[0] if buys else executions[0]
        self.entry_cut = first.at or start_of(first.day)
        if sells and self.trade.open_quantity == 0:
            last = sells[-1]
            self.exit_cut = last.at or start_of(last.day)
        else:
            self.exit_cut = self.now  # still open: everything up to now is known

    # --- helpers ----------------------------------------------------------------------

    def _instrument(self, symbol: object) -> str:
        text = str(symbol or "").strip()
        if not text or text.lower() == "this":
            return self.trade.instrument_id
        upper = text.upper()
        if upper == "SPY":
            return SPY
        if upper == "QQQ":
            return QQQ
        found = self.resolve_symbol(upper)
        if found is None:
            raise ToolInputError(f"unknown symbol {text}")
        return found

    def _windows(self, instrument_id: str, start: date, end: date) -> list[SessionWindow]:
        calendar = calendar_for(instrument_id)
        windows = []
        day = start
        try:
            while day <= end:
                window = calendar.window_for_date(day)
                if window is not None:
                    windows.append(window)
                day += timedelta(days=1)
        except CalendarError as exc:
            raise ToolInputError(str(exc)) from exc
        return windows

    def _fetch(
        self, instrument_id: str, timeframe: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        if end < start:
            raise ToolInputError("end_date is before start_date")
        windows = self._windows(instrument_id, start, end)
        if not windows:
            raise ToolInputError("no trading session in that range")
        bars: list[dict[str, Any]] = []
        try:
            if timeframe in MAX_SESSIONS:
                if len(windows) > MAX_SESSIONS[timeframe]:
                    raise ToolInputError(
                        f"{timeframe} bars cover at most {MAX_SESSIONS[timeframe]} sessions"
                    )
                for window in windows:
                    result = self.get_bars(
                        BarsQuery(
                            instrument_id, timeframe, window.pre_open_utc, window.post_close_utc
                        )
                    )
                    bars.extend(result.bars)
            else:
                if (end - start).days > MAX_DAYS[timeframe]:
                    raise ToolInputError(
                        f"{timeframe} bars cover at most {MAX_DAYS[timeframe]} days"
                    )
                result = self.get_bars(
                    BarsQuery(
                        instrument_id,
                        timeframe,
                        windows[0].pre_open_utc,
                        windows[-1].post_close_utc,
                    )
                )
                bars.extend(result.bars)
        except MarketDataError as exc:
            raise ToolInputError(f"bars not available: {exc}") from exc
        return sorted((dict(b) for b in bars), key=lambda b: str(b["interval_start_utc"]))

    def _cut(
        self, bars: list[dict[str, Any]], after_exit: bool
    ) -> tuple[list[dict[str, Any]], int]:
        """Bars up to the exit; with `after_exit`, later ones too (counted as hindsight)."""
        kept, later = [], 0
        for bar in bars:
            ended = _parse(str(bar["interval_end_utc"]))
            if ended <= self.exit_cut:
                kept.append(bar)
            elif after_exit and ended <= self.now:
                bar["hindsight"] = True
                kept.append(bar)
                later += 1
        return kept, later

    def _at(self, which: object) -> datetime:
        if which == "entry":
            return self.entry_cut
        if which == "exit":
            return self.exit_cut
        raise ToolInputError("at must be entry or exit")

    @staticmethod
    def _bar_objects(bars: Sequence[Mapping[str, Any]]) -> list[Bar]:
        return [
            Bar(
                int(_parse(str(b["interval_start_utc"])).timestamp()),
                float(b["open"]),
                float(b["high"]),
                float(b["low"]),
                float(b["close"]),
                float(b["volume"]),
            )
            for b in bars
        ]

    def _lookback(
        self, instrument_id: str, timeframe: str, cutoff: datetime
    ) -> list[dict[str, Any]]:
        """Bars that had closed by the cutoff, enough for indicator warm-up."""
        zone = calendar_for(instrument_id).zone
        end = cutoff.astimezone(zone).date()
        if timeframe in MAX_SESSIONS:
            start = end - timedelta(days=MAX_SESSIONS[timeframe] + 2)
            windows = self._windows(instrument_id, start, end)[-MAX_SESSIONS[timeframe] :]
            start = windows[0].session_date if windows else end
        else:
            start = end - timedelta(days=MAX_DAYS[timeframe])
        bars = self._fetch(instrument_id, timeframe, start, end)
        return [b for b in bars if _parse(str(b["interval_end_utc"])) <= cutoff]

    # --- tools ------------------------------------------------------------------------

    def run(self, name: str, tool_input: Mapping[str, Any]) -> ToolResult:
        if name == "get_bars":
            return self.get_bars_tool(tool_input)
        if name == "get_indicator":
            return self.indicator_tool(tool_input)
        if name == "chart_image":
            return self.chart_tool(tool_input)
        if name == "list_strategies":
            return self.list_tool()
        if name == "strategy_signals":
            return self.signals_tool(tool_input)
        raise ToolInputError(f"unknown tool {name}")

    def _range(self, tool_input: Mapping[str, Any]) -> tuple[str, str, list[dict[str, Any]], int]:
        instrument = self._instrument(tool_input.get("symbol"))
        timeframe = _timeframe(tool_input.get("timeframe"))
        start = _date(tool_input.get("start_date"), "start_date")
        end = _date(tool_input.get("end_date"), "end_date")
        bars, later = self._cut(
            self._fetch(instrument, timeframe, start, end),
            bool(tool_input.get("include_after_exit", False)),
        )
        if not bars:
            raise ToolInputError("no bars had closed by the trade's exit in that range")
        return instrument, timeframe, bars, later

    def get_bars_tool(self, tool_input: Mapping[str, Any]) -> ToolResult:
        instrument, timeframe, bars, later = self._range(tool_input)
        shown = bars[-TABLE_BARS:]
        first_open = Decimal(str(bars[0]["open"]))
        last_close = Decimal(str(bars[-1]["close"]))
        change = (last_close / first_open - 1) if first_open else Decimal(0)
        data = {
            "instrument_id": instrument,
            "timeframe": timeframe,
            "default_cut_utc": _utc(self.exit_cut),
            "bars_total": len(bars),
            "bars_listed": len(shown),
            "hindsight_bars": later,
            "summary": {
                "first_open": str(first_open),
                "last_close": str(last_close),
                "change_pct": f"{float(change) * 100:.2f}",
                "high": str(max(Decimal(str(b["high"])) for b in bars)),
                "low": str(min(Decimal(str(b["low"])) for b in bars)),
                "volume": str(sum(Decimal(str(b["volume"])) for b in bars)),
            },
            "columns": ["start_utc", "open", "high", "low", "close", "volume", "hindsight"],
            "bars": [
                [
                    b["interval_start_utc"],
                    b["open"],
                    b["high"],
                    b["low"],
                    b["close"],
                    b["volume"],
                    bool(b.get("hindsight")),
                ]
                for b in shown
            ],
        }
        return ToolResult(
            f"{instrument} {timeframe}: {len(bars)} bars, {float(change):+.2%}",
            data=data,
            hindsight=later > 0,
        )

    def indicator_tool(self, tool_input: Mapping[str, Any]) -> ToolResult:
        instrument = self._instrument(tool_input.get("symbol"))
        timeframe = _timeframe(tool_input.get("timeframe"))
        name = str(tool_input.get("indicator", ""))
        if name not in INDICATORS:
            raise ToolInputError(f"unknown indicator {name}")
        raw = tool_input.get("params") or {}
        if not isinstance(raw, Mapping):
            raise ToolInputError("params must be an object of numbers")
        try:
            params = {str(k): float(Decimal(str(v))) for k, v in raw.items()}
        except (InvalidOperation, ValueError) as exc:
            raise ToolInputError("params must be numbers") from exc
        cutoff = self._at(tool_input.get("at"))
        bars = self._lookback(instrument, timeframe, cutoff)
        if not bars:
            raise ToolInputError("no bars had closed by then")
        zone = calendar_for(instrument).zone.key
        try:
            outputs = compute(name, self._bar_objects(bars), params, zone)
        except ValueError as exc:
            raise ToolInputError(str(exc)) from exc
        values = {
            key: (None if not series or series[-1] is None else round(float(series[-1]), 6))
            for key, series in outputs.items()
        }
        data = {
            "instrument_id": instrument,
            "timeframe": timeframe,
            "indicator": name,
            "params": params,
            "bar_start_utc": bars[-1]["interval_start_utc"],
            "bar_close": bars[-1]["close"],
            "values": values,
            "bars_used": len(bars),
        }
        shown = ", ".join(f"{k}={v}" for k, v in values.items())
        return ToolResult(
            f"{name} {instrument} {timeframe} at {tool_input.get('at')}: {shown}", data=data
        )

    def chart_tool(self, tool_input: Mapping[str, Any]) -> ToolResult:
        instrument, timeframe, bars, later = self._range(tool_input)
        bars = bars[-IMAGE_BARS:]
        markers = []
        if instrument == self.trade.instrument_id:
            daily = timeframe in ("1d", "1w")
            zone = calendar_for(instrument).zone
            for execution in self.executions:
                for bar in bars:
                    start = _parse(str(bar["interval_start_utc"]))
                    end = _parse(str(bar["interval_end_utc"]))
                    if execution.at is not None and not daily:
                        hit = start <= execution.at < end
                    else:
                        hit = (
                            start.astimezone(zone).date()
                            <= execution.day
                            <= (end - timedelta(seconds=1)).astimezone(zone).date()
                        )
                    if hit:
                        markers.append(
                            {
                                "time_utc": bar["interval_start_utc"],
                                "side": execution.side,
                                "quantity": f"{execution.quantity:f}",
                                "price": f"{execution.price:f}",
                            }
                        )
                        break
        cutoff = max(_parse(str(b["interval_end_utc"])) for b in bars)
        title = (
            f"{instrument} {timeframe} {tool_input.get('start_date')} to "
            f"{tool_input.get('end_date')}" + (" (includes bars after the exit)" if later else "")
        )
        image = {
            "kind": "tool",
            "timeframe": timeframe,
            "title": title,
            "cutoff_utc": _utc(cutoff),
            "bars": [{k: v for k, v in b.items() if k != "hindsight"} for b in bars],
            "markers": markers,
        }
        return ToolResult(f"chart {title}", image=image, hindsight=later > 0)

    def list_tool(self) -> ToolResult:
        items = []
        for strategy in self.strategies():
            definition = strategy.get("definition") or {}
            items.append(
                {
                    "strategy_id": strategy["strategy_id"],
                    "name": strategy["name"],
                    "type": strategy.get("strategy_type"),
                    "linked_to_this_trade": strategy["strategy_id"] == self.trade.strategy_id,
                    "definition": definition if strategy.get("strategy_type") == "rules" else None,
                }
            )
        return ToolResult(f"{len(items)} saved strategies", data={"strategies": items})

    def signals_tool(self, tool_input: Mapping[str, Any]) -> ToolResult:
        strategy_id = tool_input.get("strategy_id") or self.trade.strategy_id
        if not strategy_id:
            raise ToolInputError("this trade has no linked strategy; pass strategy_id")
        found = next((s for s in self.strategies() if s["strategy_id"] == strategy_id), None)
        if found is None:
            raise ToolInputError(f"no strategy {strategy_id}")
        if found.get("strategy_type") != "rules":
            raise ToolInputError("only rule strategies can be evaluated here")
        try:
            # Python indicator operands need the sandbox; they are refused here.
            definition = validate_definition(found["definition"])
        except StrategyError as exc:
            raise ToolInputError(f"this strategy cannot be evaluated here: {exc}") from exc
        timeframe = _timeframe(tool_input.get("timeframe"))
        cutoff = self._at(tool_input.get("at"))
        instrument = self.trade.instrument_id
        bars = self._lookback(instrument, timeframe, cutoff)
        if not bars:
            raise ToolInputError("no bars had closed by then")
        evaluator = RuleEvaluator(
            definition, self._bar_objects(bars), calendar_for(instrument).zone.key
        )
        rows = []
        try:
            for index in range(max(0, len(bars) - SIGNAL_BARS), len(bars)):
                exit_rule = definition.get("exit")
                rows.append(
                    {
                        "bar_start_utc": bars[index]["interval_start_utc"],
                        "close": bars[index]["close"],
                        "entry_rule_held": evaluator.holds(definition["entry"], index),
                        "exit_rule_held": None
                        if exit_rule is None
                        else evaluator.holds(exit_rule, index),
                    }
                )
        except StrategyError as exc:
            raise ToolInputError(str(exc)) from exc
        data = {
            "strategy_id": strategy_id,
            "name": found["name"],
            "timeframe": timeframe,
            "at": tool_input.get("at"),
            "last_bars": rows,
        }
        held = sum(1 for row in rows if row["entry_rule_held"])
        return ToolResult(
            f"{found['name']} {timeframe} at {tool_input.get('at')}: entry rule held on "
            f"{held} of the last {len(rows)} bars",
            data=data,
        )


def as_text(result: ToolResult) -> str:
    return json.dumps(result.data, ensure_ascii=False, separators=(",", ":"))

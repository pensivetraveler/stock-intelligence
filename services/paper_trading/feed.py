"""Bars for the paper runner from the same bar service as the charts and backtests.

Each snapshot reads the instrument's recent regular sessions up to `now` as a
point-in-time cutoff, through the backtest loader (ADR 0021), so a bar that was not yet
available is never shown. A snapshot whose bars come from more than one source, feed,
adjustment or dataset revision is refused rather than stitched together.

Bars outside the regular session are read apart, only for a position's protection (ADR
0044, decision 3). Strategies never see them, as in a backtest.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from services.backtest.data import EngineBar, load_instrument
from services.market_data.calendar import CalendarError
from services.market_data.instruments import calendar_for
from services.market_data.provider import BarsQuery, BarsResult, MarketDataError
from services.paper_trading.runner import FeedSnapshot

GetBars = Callable[[BarsQuery], BarsResult]


def _engine_bar(bar: dict[str, Any]) -> EngineBar:
    def epoch(name: str) -> int:
        return int(datetime.fromisoformat(str(bar[name]).replace("Z", "+00:00")).timestamp())

    return EngineBar(
        epoch("interval_start_utc"),
        epoch("interval_end_utc"),
        Decimal(bar["open"]),
        Decimal(bar["high"]),
        Decimal(bar["low"]),
        Decimal(bar["close"]),
        Decimal(bar["volume"]),
        bool(bar["is_final"]),
    )


class ServiceBarFeed:
    def __init__(self, get_bars: GetBars, timeframe: str = "1m", lookback_days: int = 7) -> None:
        self._get_bars = get_bars
        self.timeframe = timeframe
        self._lookback = timedelta(days=lookback_days)
        self.last_error: str | None = None

    def snapshot(self, instrument_id: str, now: datetime) -> FeedSnapshot | None:
        now = now.replace(microsecond=0)  # bar queries take whole seconds
        try:
            item = load_instrument(
                self._get_bars,
                instrument_id,
                self.timeframe,
                (now - self._lookback).date(),
                now.date(),
                as_of_utc=now,
            )
        except (MarketDataError, OSError, ValueError) as exc:
            self.last_error = f"{instrument_id}: {type(exc).__name__}: {str(exc)[:200]}"
            return None
        pin = item.pin
        single = all(
            len(pin[key]) == 1
            for key in ("source_ids", "feed_scopes", "adjustments", "dataset_revisions")
        )
        if not item.bars or not single:
            self.last_error = None if item.bars else f"{instrument_id}: no bars"
            if not single:
                self.last_error = f"{instrument_id}: bars from more than one source or revision"
            return None
        self.last_error = None
        return FeedSnapshot(
            bars=item.bars,
            data_as_of_utc=now,
            timeframe=self.timeframe,
            source_id=pin["source_ids"][0],
            feed_scope=pin["feed_scopes"][0],
            dataset_revision=pin["dataset_revisions"][0],
            adjustment=pin["adjustments"][0],
            session="regular",
            calendar_id=pin["calendar_id"],
            calendar_version=pin["calendar_version"],
        )

    def outside(self, instrument_id: str, since: int, now: datetime) -> list[EngineBar]:
        """Pre- and post-market bars from `since` (epoch seconds), up to `now` as a cutoff.

        Empty when they cannot be read or are not from one source: regular bars still judge.
        """
        now = now.replace(microsecond=0)
        start = max(datetime.fromtimestamp(since, timezone.utc), now - self._lookback)
        found: list[dict[str, Any]] = []
        try:
            calendar = calendar_for(instrument_id)
            day = start.date() - timedelta(days=1)
            while day <= now.date():
                window = calendar.window_for_date(day)
                day += timedelta(days=1)
                if window is None:
                    continue
                for low, high in (
                    (window.pre_open_utc, window.regular_open_utc),
                    (window.regular_close_utc, window.post_close_utc),
                ):
                    if high > start and low < now:
                        query = BarsQuery(instrument_id, "1m", low, high, as_of_utc=now)
                        found.extend(self._get_bars(query).bars)
        except (MarketDataError, CalendarError, OSError, ValueError) as exc:
            self.last_error = f"{instrument_id}: {type(exc).__name__}: {str(exc)[:200]}"
            return []
        if len({(bar["source_id"], bar["source_feed_scope"]) for bar in found}) > 1:
            self.last_error = f"{instrument_id}: outside bars from more than one source"
            return []
        return sorted(
            (_engine_bar(bar) for bar in found if bar["session"] != "regular"),
            key=lambda bar: bar.time,
        )

"""Bars for the paper runner from the same bar service as the charts and backtests.

Each snapshot reads the instrument's recent regular sessions up to `now` as a
point-in-time cutoff, through the backtest loader (ADR 0021), so a bar that was not yet
available is never shown. A snapshot whose bars come from more than one source, feed,
adjustment or dataset revision is refused rather than stitched together.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from services.backtest.data import load_instrument
from services.market_data.provider import BarsQuery, BarsResult, MarketDataError
from services.paper_trading.runner import FeedSnapshot

GetBars = Callable[[BarsQuery], BarsResult]


class ServiceBarFeed:
    def __init__(self, get_bars: GetBars, timeframe: str = "1m", lookback_days: int = 7) -> None:
        self._get_bars = get_bars
        self.timeframe = timeframe
        self._lookback = timedelta(days=lookback_days)
        self.last_error: str | None = None

    def snapshot(self, instrument_id: str, now: datetime) -> FeedSnapshot | None:
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

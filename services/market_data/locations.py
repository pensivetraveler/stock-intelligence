"""Where the market data store lives (ADR 0046 step 3).

The store (`metadata.sqlite3`, `bars/`, `ticks/`) sits in `SIW_DATA_DIR` unless
`SIW_MARKET_DATA_DIR` names another folder, normally `$STOCK_SHARED_DATA/market`. Every
program that opens the store asks this module, so the store is never split in two.
"""

from __future__ import annotations

import os
from pathlib import Path

MARKET_DATA_DIR_ENV = "SIW_MARKET_DATA_DIR"
STORE_FILE = "metadata.sqlite3"


class MarketStoreLocationError(RuntimeError):
    """The settings would open a second, empty store next to the real one."""


def market_data_dir(data_dir: Path | None) -> Path | None:
    """The market store folder for a SIW data folder, or None without either setting.

    With `SIW_MARKET_DATA_DIR` set, a store still left in `data_dir` is refused: opening
    the new folder would start an empty store and leave the cached bars behind.
    """
    configured = os.getenv(MARKET_DATA_DIR_ENV, "").strip()
    if not configured:
        return data_dir
    market = Path(configured).expanduser()
    if data_dir is not None and market.resolve() != data_dir.resolve():
        left = data_dir / STORE_FILE
        if left.exists() and not (market / STORE_FILE).exists():
            raise MarketStoreLocationError(
                f"{MARKET_DATA_DIR_ENV} names {market}, but the market data store is still "
                f"in {data_dir}: move metadata.sqlite3, bars and ticks there first"
            )
    return market


def market_data_dir_from_env() -> Path | None:
    """`market_data_dir` for `SIW_DATA_DIR`."""
    configured = os.getenv("SIW_DATA_DIR", "").strip()
    return market_data_dir(Path(configured) if configured else None)

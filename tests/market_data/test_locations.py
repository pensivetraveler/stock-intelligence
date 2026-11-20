"""The market data store's folder (ADR 0046 step 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.market_data.locations import (
    MarketStoreLocationError,
    market_data_dir,
    market_data_dir_from_env,
)


def test_without_the_setting_the_store_stays_in_the_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SIW_MARKET_DATA_DIR", raising=False)
    assert market_data_dir(tmp_path) == tmp_path
    assert market_data_dir(None) is None
    monkeypatch.setenv("SIW_DATA_DIR", str(tmp_path))
    assert market_data_dir_from_env() == tmp_path


def test_the_setting_names_the_shared_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, market = tmp_path / "data", tmp_path / "shared" / "market"
    data.mkdir()
    monkeypatch.setenv("SIW_MARKET_DATA_DIR", str(market))
    assert market_data_dir(data) == market
    # Moved: the store is in the new folder, nothing is left behind.
    market.mkdir(parents=True)
    (market / "metadata.sqlite3").write_bytes(b"")
    assert market_data_dir(data) == market


def test_a_store_left_behind_is_refused_rather_than_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, market = tmp_path / "data", tmp_path / "market"
    data.mkdir()
    (data / "metadata.sqlite3").write_bytes(b"")
    monkeypatch.setenv("SIW_MARKET_DATA_DIR", str(market))
    with pytest.raises(MarketStoreLocationError, match="still in"):
        market_data_dir(data)


def test_a_home_relative_setting_is_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIW_MARKET_DATA_DIR", "~/Trading/stock-data/market")
    assert market_data_dir(None) == Path.home() / "Trading/stock-data/market"

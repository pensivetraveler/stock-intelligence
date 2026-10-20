"""Toss research data as display tables: rankings, market indicators, sectors and
Korean-market trading trends (official OpenAPI 1.2.24).

These are reference tables for reading, not inputs to charts, indicators or backtests, so
they share one generic contract (`research_table`): every value stays an exact string as
Toss sent it (no float), times are converted to UTC, and nested fields become dotted
column names (`foreigner.netBuyVolume`). Read on demand; nothing is stored.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from packages.contracts.validator import validate_document
from services.market_data.provider import InvalidBarsQueryError, ProviderDataError
from services.market_data.toss_session import TossSession

SOURCE_ID = "toss-us"
PARAM_VALUE = re.compile(r"^[A-Za-z0-9._,:+\-]{1,400}$")
MAX_COLUMNS = 80
MAX_ROWS = 2_000
TIME_KEYS = re.compile(r"(At|timestamp|Time)$")
TREND_PARAMS = frozenset({"count", "until"})


@dataclass(frozen=True)
class Dataset:
    path: str
    params: frozenset[str]
    rows: str | None  # key of the row list in `result`; None: `result` itself
    path_param: str | None = None  # name of the one path value ({symbol}, {sectorId})
    as_of: str | None = None
    next_key: str | None = None
    single: bool = False  # `result` is one object, shown as one row


DATASETS: dict[str, Dataset] = {
    "rankings": Dataset(
        "/api/v1/rankings",
        frozenset({"type", "marketCountry", "duration", "excludeInvestmentCaution", "count"}),
        "rankings",
        as_of="rankedAt",
    ),
    "market_indicator_prices": Dataset(
        "/api/v1/market-indicators/prices", frozenset({"symbols"}), None
    ),
    "market_indicator_candles": Dataset(
        "/api/v1/market-indicators/{symbol}/candles",
        frozenset({"interval", "count", "before"}),
        "candles",
        "symbol",
        next_key="nextBefore",
    ),
    "market_indicator_investor_trading": Dataset(
        "/api/v1/market-indicators/{symbol}/investor-trading",
        frozenset({"interval", "count", "until"}),
        "records",
        "symbol",
        next_key="nextUntil",
    ),
    "sectors": Dataset("/api/v1/sectors", frozenset(), "sectors"),
    "sector_rankings": Dataset(
        "/api/v1/sectors/rankings",
        frozenset({"type", "marketCountry", "duration"}),
        "rankings",
    ),
    "sector": Dataset(
        "/api/v1/sectors/{sectorId}",
        frozenset({"marketCountry"}),
        None,
        "sectorId",
        single=True,
    ),
    "sector_stocks": Dataset(
        "/api/v1/sectors/{sectorId}/stocks", frozenset({"marketCountry"}), "stocks", "sectorId"
    ),
    "sector_etfs": Dataset(
        "/api/v1/sectors/{sectorId}/etfs", frozenset({"marketCountry"}), "etfs", "sectorId"
    ),
    "stock_investor_trading": Dataset(
        "/api/v1/stocks/{symbol}/investor-trading",
        TREND_PARAMS,
        "records",
        "symbol",
        next_key="nextUntil",
    ),
    "stock_program_trades": Dataset(
        "/api/v1/stocks/{symbol}/program-trades",
        TREND_PARAMS,
        "records",
        "symbol",
        next_key="nextUntil",
    ),
    "stock_short_selling": Dataset(
        "/api/v1/stocks/{symbol}/short-selling",
        TREND_PARAMS,
        "records",
        "symbol",
        next_key="nextUntil",
    ),
    "stock_credit_trades": Dataset(
        "/api/v1/stocks/{symbol}/credit-trades",
        TREND_PARAMS,
        "records",
        "symbol",
        next_key="nextUntil",
    ),
    "stock_securities_lending": Dataset(
        "/api/v1/stocks/{symbol}/securities-lending",
        TREND_PARAMS,
        "records",
        "symbol",
        next_key="nextUntil",
    ),
}


def _utc(value: str) -> str:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if stamp.tzinfo is None:
        return value
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _cell(key: str, value: object) -> str | bool | None:
    if value is None or isinstance(value, bool):
        return value
    text = str(value)[:400]
    return _utc(text) if TIME_KEYS.search(key) else text


def _flatten(prefix: str, value: object, out: dict[str, str | bool | None]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, out)
    elif isinstance(value, list):
        # Lists of {duration, changeRate} pairs (sector performance) become one column each.
        if all(isinstance(item, dict) and "duration" in item for item in value) and value:
            for item in value:
                rest = {k: v for k, v in item.items() if k != "duration"}
                _flatten(f"{prefix}.{item['duration']}", rest, out)
        else:
            out[prefix] = ", ".join(str(item) for item in value)[:400]
    else:
        out[prefix] = _cell(prefix, value)


def research_table(
    session: TossSession,
    dataset: str,
    key: str | None,
    params: Mapping[str, str],
    now: Callable[[], datetime],
) -> dict[str, Any]:
    spec = DATASETS.get(dataset)
    if spec is None:
        raise InvalidBarsQueryError(f"unknown research dataset {dataset!r}")
    if (spec.path_param is None) != (key is None):
        need = "takes no key" if spec.path_param is None else f"needs a {spec.path_param}"
        raise InvalidBarsQueryError(f"{dataset} {need}")
    unknown = set(params) - spec.params
    if unknown:
        raise InvalidBarsQueryError(f"{dataset} does not take {', '.join(sorted(unknown))}")
    for name, value in params.items():
        if not PARAM_VALUE.fullmatch(value):
            raise InvalidBarsQueryError(f"invalid {name} value")
    result = session.get(
        spec.path,
        path={spec.path_param: key} if spec.path_param and key is not None else None,
        params=dict(params),
    )
    if spec.single:
        items: object = [result]
    elif spec.rows is None:
        items = result
    else:
        items = result.get(spec.rows) if isinstance(result, dict) else None
    if not isinstance(items, list):
        raise ProviderDataError(f"Toss returned no rows for {dataset}")
    flat: list[dict[str, str | bool | None]] = []
    for item in items[:MAX_ROWS]:
        row: dict[str, str | bool | None] = {}
        _flatten("", item, row)
        flat.append(row)
    columns: list[str] = []
    for row in flat:
        for column in row:
            if column not in columns and len(columns) < MAX_COLUMNS:
                columns.append(column)
    meta = result if isinstance(result, dict) else {}
    as_of = meta.get(spec.as_of) if spec.as_of else None
    next_cursor = meta.get(spec.next_key) if spec.next_key else None
    document = {
        "kind": "research_table",
        "schema_version": "0.1",
        "source_id": SOURCE_ID,
        "dataset": dataset,
        "key": key,
        "params": dict(params),
        "observed_at_utc": now()
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "as_of_utc": _utc(str(as_of)) if as_of else None,
        "next_cursor": str(next_cursor)[:100] if next_cursor else None,
        "columns": columns,
        "rows": [[row.get(column) for column in columns] for row in flat],
    }
    try:
        validate_document(document)
    except ValueError as exc:
        raise ProviderDataError(f"Toss research data violates the contract: {exc}") from exc
    return document

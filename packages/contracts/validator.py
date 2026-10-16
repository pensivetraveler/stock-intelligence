from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_PATH = Path(__file__).with_name("schemas") / "contracts.schema.json"


def load_schema() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def schema_validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _point_in_time_errors(bars: list[dict[str, Any]], coverage: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    requested_start = _utc(coverage["requested_start_utc"])
    requested_end = _utc(coverage["requested_end_utc"])
    cutoff_value = coverage["point_in_time_cutoff_utc"]
    cutoff = _utc(cutoff_value) if cutoff_value is not None else None
    previous_start: datetime | None = None
    for bar in bars:
        start = _utc(bar["interval_start_utc"])
        end = _utc(bar["interval_end_utc"])
        if start < requested_start or end > requested_end:
            errors.append("response bar must lie within the requested coverage range")
        if previous_start is not None and start <= previous_start:
            errors.append("response bars must be strictly ordered by interval_start_utc")
        previous_start = start
        if cutoff is not None and _utc(bar["available_at_utc"]) > cutoff:
            errors.append("response bar was not available at the point-in-time cutoff")
    return errors


def semantic_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    kind = document.get("kind")

    if kind == "price_bar":
        start = _utc(document["interval_start_utc"])
        end = _utc(document["interval_end_utc"])
        if end <= start:
            errors.append("interval_end_utc must be after interval_start_utc")
        open_price = Decimal(document["open"])
        high = Decimal(document["high"])
        low = Decimal(document["low"])
        close = Decimal(document["close"])
        if high < max(open_price, close, low):
            errors.append("high must be greater than or equal to open, close, and low")
        if low > min(open_price, close, high):
            errors.append("low must be less than or equal to open, close, and high")
        lineage = cast(dict[str, Any], document["lineage"])
        expected = cast(int, lineage["expected_source_bar_count"])
        observed = cast(int, lineage["observed_source_bar_count"])
        if observed > expected:
            errors.append("observed source bar count cannot exceed expected source bar count")
        if document["quality"] == "complete" and (observed != expected or not document["is_final"]):
            errors.append("complete bar quality requires all source bars and finalization")
        if document["quality"] == "provisional" and document["is_final"]:
            errors.append("provisional bar quality cannot be final")
        if document["is_final"] and _utc(document["available_at_utc"]) < end:
            errors.append("a final bar cannot be available before its interval ends")
        if lineage["kind"] == "provider" and (
            expected != 1 or observed != 1 or lineage["source_dataset_revision"] is not None
        ):
            errors.append("provider lineage must represent one direct source bar")
        if lineage["kind"] == "aggregated_1m" and (
            document["timeframe"] == "1m" or lineage["source_dataset_revision"] is None
        ):
            errors.append("aggregated lineage requires a derived timeframe and source revision")

    if kind == "manual_execution":
        start = _utc(document["selected_interval_start_utc"])
        end = _utc(document["selected_interval_end_utc"])
        if end <= start:
            errors.append("selected interval end must be after start")

    if kind == "backtest_run_config" and _utc(document["end_utc"]) <= _utc(document["start_utc"]):
        errors.append("backtest end_utc must be after start_utc")

    if kind == "coverage_manifest":
        if _utc(document["requested_end_utc"]) <= _utc(document["requested_start_utc"]):
            errors.append("coverage requested_end_utc must be after requested_start_utc")
        observed = cast(int, document["observed_interval_count"])
        expected = cast(int, document["expected_interval_count"])
        missing = cast(list[object], document["missing_intervals"])
        status = document["coverage_status"]
        if observed + len(missing) != expected:
            errors.append("coverage observed plus missing intervals must equal expected intervals")
        if status == "complete" and (observed != expected or missing):
            errors.append("complete coverage must contain every expected interval")
        if status == "partial" and not (0 < observed < expected):
            errors.append("partial coverage must contain some but not all expected intervals")
        if status == "empty" and observed != 0:
            errors.append("empty coverage must not contain observed intervals")

    if kind == "market_bars_response":
        bars = cast(list[dict[str, Any]], document["bars"])
        coverage = cast(dict[str, Any], document["coverage"])
        errors.extend(semantic_errors(coverage))
        for bar in bars:
            errors.extend(semantic_errors(bar))
            for key in (
                "instrument_id",
                "timeframe",
                "source_id",
                "source_feed_scope",
                "adjustment",
                "dataset_revision",
            ):
                if bar[key] != coverage[key]:
                    errors.append(f"response bar {key} must match coverage")
        if len(bars) != coverage["observed_interval_count"]:
            errors.append("response bar count must match observed coverage interval count")
        errors.extend(_point_in_time_errors(bars, coverage))

    return errors


def validation_errors(document: dict[str, Any]) -> list[str]:
    validator = schema_validator()
    schema_errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if schema_errors:
        return [error.message for error in schema_errors]
    return semantic_errors(document)


def validate_document(document: dict[str, Any]) -> None:
    errors = validation_errors(document)
    if errors:
        raise ValueError("; ".join(errors))

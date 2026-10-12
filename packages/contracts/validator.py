from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_PATH = Path(__file__).with_name("schemas") / "contracts.schema.json"


def load_schema() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def schema_validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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

    if kind == "manual_execution":
        start = _utc(document["selected_interval_start_utc"])
        end = _utc(document["selected_interval_end_utc"])
        if end <= start:
            errors.append("selected interval end must be after start")

    if kind == "backtest_run_config" and _utc(document["end_utc"]) <= _utc(document["start_utc"]):
        errors.append("backtest end_utc must be after start_utc")

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

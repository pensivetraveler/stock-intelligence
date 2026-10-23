from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from functools import cache, lru_cache
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


# Kinds whose currency must match the instrument's market (kr: listings trade in KRW).
CURRENCY_BY_MARKET_KINDS = frozenset(
    {
        "manual_execution",
        "scenario_definition",
        "imported_execution",
        "broker_order_execution",
        "broker_confirmed_execution",
        "market_quote",
        "paper_order_intent",
        "paper_execution",
    }
)


@cache
def _kind_validator(kind: str) -> Draft202012Validator | None:
    """Validator for one contract kind, or None when the kind is unknown.

    The root schema is a `oneOf` over every kind, so validating a bar tries all
    of them. Each branch pins `kind` with `const`, so checking only the matching
    definition accepts and rejects exactly the same documents. Local `$ref`s are
    also inlined once, which halves the per-document cost again.
    """
    schema = load_schema()
    for branch in schema["oneOf"]:
        name = cast(str, branch["$ref"]).rsplit("/", 1)[-1]
        if schema["$defs"][name]["properties"]["kind"].get("const") == kind:
            inlined = _inline_refs(schema["$defs"][name], schema["$defs"], (name,))
            return Draft202012Validator(
                {"$schema": schema["$schema"], **inlined}, format_checker=FormatChecker()
            )
    return None


def _inline_refs(node: Any, defs: dict[str, Any], stack: tuple[str, ...]) -> Any:
    """Replace `{"$ref": "#/$defs/X", ...}` with X's definition (no recursive defs)."""
    if isinstance(node, list):
        return [_inline_refs(item, defs, stack) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        name = ref.removeprefix("#/$defs/")
        if name in stack:
            raise ValueError(f"recursive contract definition cannot be inlined: {name}")
        target = _inline_refs(defs[name], defs, (*stack, name))
        siblings = {key: _inline_refs(value, defs, stack) for key, value in node.items()}
        siblings.pop("$ref")
        return {**target, **siblings}
    return {key: _inline_refs(value, defs, stack) for key, value in node.items()}


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


def _paper_intent_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    data = document["data"]
    bar_start = _utc(data["signal_bar_start_utc"])
    bar_end = _utc(data["signal_bar_end_utc"])
    if bar_end <= bar_start:
        errors.append("signal bar end must be after its start")
    if _utc(data["data_as_of_utc"]) < bar_end:
        errors.append("a signal cannot use a bar that had not ended at the data cutoff")
    if _utc(document["created_at_utc"]) < _utc(data["data_as_of_utc"]):
        errors.append("an intent cannot be created before its data cutoff")
    order = document["order"]
    if Decimal(order["quantity"]) <= 0:
        errors.append("intent quantity must be positive")
    if order["order_type"] == "limit" and (
        order["limit_price"] is None
        or Decimal(order["limit_price"]) <= 0
        or order["estimated_max_price"] is not None
        or order["market_buffer_bps"] is not None
    ):
        errors.append("a limit order has a positive limit price and no market estimate")
    if order["order_type"] == "market" and (
        order["limit_price"] is not None
        or order["estimated_max_price"] is None
        or order["market_buffer_bps"] is None
        or Decimal(order["estimated_max_price"]) <= 0
    ):
        errors.append("a market order has an estimated maximum price and no limit price")
    check = document["limit_check"]
    if check["passed"] == bool(check["reasons"]):
        errors.append("a limit check lists reasons exactly when it fails")
    if check["passed"] and check["limits"]["max_order_amount_krw"] is not None:
        if check["order_amount_krw"] is None:
            errors.append("a passed amount limit must record the order amount in KRW")
        if document["currency"] == "USD" and document["fx"] is None:
            errors.append("a USD order checked against a KRW limit must record its exchange rate")
    if (
        check["passed"]
        and document["instrument_id"] not in check["limits"]["allowed_instrument_ids"]
    ):
        errors.append("a passed intent must be for an allowed instrument")
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
        if lineage["kind"] in ("aggregated_1m", "aggregated_1d") and (
            document["timeframe"] == lineage["source_timeframe"]
            or lineage["source_dataset_revision"] is None
        ):
            errors.append("aggregated lineage requires a derived timeframe and source revision")
        expected_source = {"aggregated_1m": "1m", "aggregated_1d": "1d"}.get(lineage["kind"])
        if expected_source is not None and lineage["source_timeframe"] != expected_source:
            errors.append(f"{lineage['kind']} lineage must come from {expected_source} bars")
        if lineage["kind"] == "provider" and lineage["source_timeframe"] != document["timeframe"]:
            errors.append("provider lineage must name the bar's own timeframe as its source")

    if kind == "manual_execution":
        start = _utc(document["selected_interval_start_utc"])
        end = _utc(document["selected_interval_end_utc"])
        if end <= start:
            errors.append("selected interval end must be after start")
        if Decimal(document["quantity"]) <= 0 or Decimal(document["price"]) <= 0:
            errors.append("manual execution quantity and price must be positive")
        executed_at = document["executed_at_utc"]
        if (executed_at is None) != (document["time_precision"] == "unknown"):
            errors.append("executed_at_utc is null exactly when time_precision is unknown")
        if executed_at is not None and _utc(executed_at) > _utc(document["asserted_at_utc"]):
            errors.append("an execution cannot be claimed for a time after it was recorded")

    if kind == "scenario_definition":
        if Decimal(document["quantity"]) <= 0:
            errors.append("scenario quantity must be positive")
        if Decimal(document["entry_price"]) <= 0 or Decimal(document["exit_price"]) <= 0:
            errors.append("scenario entry and exit prices must be positive")
        if _utc(document["exit_interval_start_utc"]) <= _utc(document["entry_interval_start_utc"]):
            errors.append("scenario exit must come after its entry")

    if kind == "session_window":
        boundaries = [
            document[key]
            for key in ("pre_open_utc", "regular_open_utc", "regular_close_utc", "post_close_utc")
        ]
        if not document["is_trading_day"]:
            if any(value is not None for value in boundaries) or document["is_early_close"]:
                errors.append("a non-trading day has no session boundaries or early close")
        elif any(value is None for value in boundaries):
            errors.append("a trading day requires every session boundary")
        else:
            pre_open, regular_open, regular_close, post_close = (_utc(v) for v in boundaries)
            if not pre_open <= regular_open < regular_close <= post_close:
                errors.append("session boundaries must be ordered pre <= open < close <= post")

    if kind == "market_instruments_response":
        identifiers = [item["instrument"]["instrument_id"] for item in document["instruments"]]
        if len(identifiers) != len(set(identifiers)):
            errors.append("instrument_id must be unique within the response")

    if kind in CURRENCY_BY_MARKET_KINDS:
        instrument = document.get("instrument_id")
        if isinstance(instrument, str) and document.get("currency") in ("USD", "KRW"):
            trades_in = "KRW" if instrument.startswith("kr:") else "USD"
            if document["currency"] != trades_in:
                errors.append(f"{instrument} trades in {trades_in}")

    if kind == "research_table":
        width = len(document["columns"])
        if len(set(document["columns"])) != width:
            errors.append("research_table columns must be unique")
        if any(len(row) != width for row in document["rows"]):
            errors.append("every research_table row must have one cell per column")

    if kind == "symbol_search_response":
        identifiers = [
            item["listing"]["instrument"]["instrument_id"] for item in document["results"]
        ]
        if len(identifiers) != len(set(identifiers)):
            errors.append("instrument_id must be unique within the response")

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

    if kind == "imported_execution":
        if Decimal(document["quantity"]) <= 0 or Decimal(document["price"]) <= 0:
            errors.append("imported execution quantity and price must be positive")
        review = document["duplicate_review"]
        if review["decision"] == "imported_after_review" and not review["related"]:
            errors.append("a reviewed duplicate must name the records it resembles")

    if kind in {"import_preview_response", "imported_ledger_response"}:
        rows = document.get("rows")
        executions = (
            [row["execution"] for row in rows if row["execution"] is not None]
            if rows is not None
            else document["executions"]
        )
        for execution in executions:
            errors.extend(semantic_errors(execution))
        if rows is not None:
            for row in rows:
                if (row["status"] == "invalid") != (row["execution"] is None):
                    errors.append("only invalid preview rows lack an execution")
                if row["status"] == "invalid" and not row["errors"]:
                    errors.append("an invalid preview row must say why")
            counts = {status: 0 for status in document["summary"]}
            for row in rows:
                counts[row["status"]] += 1
            if counts != document["summary"]:
                errors.append("preview summary must count the rows by status")

    if kind == "manual_ledger_response":
        for execution in document["executions"]:
            errors.extend(semantic_errors(execution))

    if kind == "scenario_ledger_response":
        for item in document["scenarios"]:
            errors.extend(semantic_errors(item["scenario"]))

    if kind == "paper_order_intent":
        errors.extend(_paper_intent_errors(document))

    if kind == "paper_order_submission":
        responded = document["responded_at_utc"]
        if responded is not None and _utc(responded) < _utc(document["requested_at_utc"]):
            errors.append("a submission cannot be answered before it was requested")
        if document["status"] == "pending_response" and responded is not None:
            errors.append("a pending submission has no response time")
        if document["status"] in ("accepted", "rejected") and responded is None:
            errors.append("an accepted or rejected submission records when it was answered")
        if document["status"] == "accepted" and document["broker_order_ref"] is None:
            errors.append("an accepted submission must carry the broker's order reference")

    if kind == "paper_execution":
        if Decimal(document["quantity"]) <= 0 or Decimal(document["price"]) <= 0:
            errors.append("paper execution quantity and price must be positive")
        if (document["executed_at_utc"] is None) != (document["time_meaning"] == "time_unresolved"):
            errors.append("executed_at_utc is null exactly when its time is unresolved")

    if kind == "paper_intent_list_response":
        for item in document["intents"]:
            errors.extend(semantic_errors(item["intent"]))
            submission = item["submission"]
            if submission is not None:
                errors.extend(semantic_errors(submission))
                if submission["intent_id"] != item["intent"]["intent_id"]:
                    errors.append("a listed submission must belong to its intent")

    if kind == "paper_execution_list_response":
        for execution in document["executions"]:
            errors.extend(semantic_errors(execution))

    if kind == "paper_runner_status":
        if document["armed"] and (document["halted"] or document["approval_id"] is None):
            errors.append("an armed runner must be unhalted and name its approval")
        if document["halted"] != (document["halted_reason"] is not None):
            errors.append("halted_reason is set exactly while the runner is halted")

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
    kind = document.get("kind") if isinstance(document, dict) else None
    validator = (_kind_validator(kind) if isinstance(kind, str) else None) or schema_validator()
    schema_errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if schema_errors:
        return [error.message for error in schema_errors]
    return semantic_errors(document)


def validate_document(document: dict[str, Any]) -> None:
    errors = validation_errors(document)
    if errors:
        raise ValueError("; ".join(errors))

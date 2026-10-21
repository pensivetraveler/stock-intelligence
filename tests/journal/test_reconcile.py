from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from packages.contracts.validator import validate_document
from services.journal.reconcile import Parameters, reconcile

AAPL = "us:aapl:xnas:2026"
MSFT = "us:msft:xnas:2026"


def broker(order_id: str, **changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "kind": "broker_order_execution",
        "schema_version": "0.1",
        "execution_id": f"brk-{order_id}",
        "instrument_id": AAPL,
        "symbol": "AAPL",
        "side": "buy",
        "quantity": "10",
        "price": "250.00",
        "fees": "0.10",
        "commission": "0.10",
        "tax": "0",
        "currency": "USD",
        "executed_at_utc": "2026-10-09T14:00:30Z",
        "time_meaning": "reported_last_fill",
        "granularity": "order_aggregate",
        "order_type": "LIMIT",
        "order_status": "FILLED",
        "ordered_at_utc": "2026-10-09T13:59:00Z",
        "settlement_date": "2026-10-13",
        "broker_id": "toss",
        "broker_account_ref": "toss-abc-1",
        "broker_order_id": order_id,
        "verification_status": "broker_api_reported",
        "synced_at_utc": "2026-10-10T00:00:00Z",
    }
    document.update(changes)
    validate_document(document)
    return document


def manual(execution_id: str, **changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "kind": "manual_execution",
        "schema_version": "0.1",
        "execution_id": execution_id,
        "instrument_id": AAPL,
        "side": "buy",
        "quantity": "10",
        "price": "250.00",
        "currency": "USD",
        "asserted_at_utc": "2026-10-09T20:00:00Z",
        "time_precision": "minute",
        "selected_interval_start_utc": "2026-10-09T14:00:00Z",
        "selected_interval_end_utc": "2026-10-09T14:01:00Z",
        "evidence_status": "unverified",
        "executed_at_utc": "2026-10-09T14:00:00Z",
        "fees": "0",
        "memo": "",
    }
    document.update(changes)
    validate_document(document)
    return document


def imported(execution_id: str, row: int, **changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "kind": "imported_execution",
        "schema_version": "0.1",
        "execution_id": execution_id,
        "instrument_id": AAPL,
        "side": "buy",
        "quantity": "10",
        "price": "250.00",
        "fees": "0",
        "currency": "USD",
        "executed_at_utc": "2026-10-09T14:00:30Z",
        "time_precision": "exact",
        "broker_label": "Toss",
        "broker_execution_id": None,
        "source": {"batch_id": "batch-0001", "file_sha256": "a" * 64, "row_number": row},
        "verification_status": "statement_imported",
        "duplicate_review": {"decision": "none", "related": []},
    }
    document.update(changes)
    validate_document(document)
    return document


def by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {record["execution_id"]: record for record in result["records"]}


def test_one_to_one_matches_in_both_ledgers_without_touching_inputs() -> None:
    inputs = ([broker("o1")], [manual("man-0001")], [imported("imp-0001", 1)])
    before = copy.deepcopy(inputs)

    result = reconcile(*inputs)
    records = by_id(result)

    assert inputs == before
    assert {record["status"] for record in records.values()} == {"matched"}
    assert records["brk-o1"]["counterparts"] == [
        {
            "ledger": "manual_actual",
            "execution_id": "man-0001",
            "relation": "matched",
            "quantity_difference": "0",
            "price_difference_bps": "0",
        },
        {
            "ledger": "statement_imported",
            "execution_id": "imp-0001",
            "relation": "matched",
            "quantity_difference": "0",
            "price_difference_bps": "0",
        },
    ]
    assert result["summary"] == {
        "matched": 3,
        "conflicting": 0,
        "broker_only": 0,
        "manual_only": 0,
        "imported_only": 0,
    }


def test_price_tolerance_is_decimal_basis_points() -> None:
    # 250.25 is 10 bps above 250.00: inside the default tolerance; 250.26 is not.
    inside = reconcile([broker("o1")], [manual("man-0001", price="250.25")], [])
    outside = reconcile([broker("o1")], [manual("man-0001", price="250.26")], [])
    strict = reconcile(
        [broker("o1")],
        [manual("man-0001", price="250.25")],
        [],
        parameters=Parameters(price_tolerance_bps=Decimal(5)),
    )

    assert by_id(inside)["man-0001"]["status"] == "matched"
    assert by_id(outside)["man-0001"]["status"] == "conflicting"
    assert by_id(outside)["man-0001"]["reasons"] == ["price_differs"]
    assert by_id(outside)["brk-o1"]["counterparts"][0]["price_difference_bps"] == "10.4"
    assert by_id(strict)["man-0001"]["status"] == "conflicting"


def test_quantity_difference_at_the_same_time_is_a_conflict() -> None:
    result = reconcile([broker("o1")], [manual("man-0001", quantity="8")], [])
    records = by_id(result)

    assert records["man-0001"]["status"] == "conflicting"
    assert records["man-0001"]["reasons"] == ["quantity_differs"]
    assert records["brk-o1"]["status"] == "conflicting"
    assert records["brk-o1"]["counterparts"][0]["quantity_difference"] == "-2"


def test_times_outside_the_window_and_other_sides_do_not_pair() -> None:
    result = reconcile(
        [broker("o1")],
        [
            manual("man-0001", executed_at_utc="2026-10-09T16:00:00Z"),
            manual("man-0002", side="sell"),
        ],
        [],
    )
    records = by_id(result)

    assert records["brk-o1"]["status"] == "broker_only"
    assert records["brk-o1"]["reasons"] == ["no_manual_counterpart", "no_imported_counterpart"]
    assert records["man-0001"]["status"] == "manual_only"
    assert records["man-0001"]["reasons"] == ["no_broker_counterpart"]
    assert records["man-0002"]["status"] == "manual_only"


def test_minute_precision_covers_the_whole_minute_and_approximate_a_wider_window() -> None:
    result = reconcile(
        [
            broker(
                "o1", ordered_at_utc="2026-10-09T14:00:40Z", executed_at_utc="2026-10-09T14:00:50Z"
            )
        ],
        [
            manual(
                "man-0001",
                time_precision="approximate",
                executed_at_utc="2026-10-09T16:00:00Z",
                selected_interval_start_utc="2026-10-09T16:00:00Z",
                selected_interval_end_utc="2026-10-09T16:01:00Z",
            )
        ],
        [imported("imp-0001", 1, time_precision="minute", executed_at_utc="2026-10-09T14:00:00Z")],
    )
    records = by_id(result)

    assert records["imp-0001"]["status"] == "matched"
    assert records["man-0001"]["status"] == "matched"
    assert records["man-0001"]["reasons"] == ["time_approximate"]


def test_unknown_time_matches_only_a_single_fitting_order_recorded_before_it() -> None:
    unknown = manual(
        "man-0001",
        time_precision="unknown",
        executed_at_utc=None,
        asserted_at_utc="2026-10-09T20:00:00Z",
    )
    single = reconcile([broker("o1")], [unknown], [])
    ambiguous = reconcile(
        [
            broker("o1"),
            broker(
                "o2", ordered_at_utc="2026-10-09T15:00:00Z", executed_at_utc="2026-10-09T15:01:00Z"
            ),
        ],
        [unknown],
        [],
    )
    after_assertion = reconcile(
        [
            broker(
                "o3", ordered_at_utc="2026-10-10T14:00:00Z", executed_at_utc="2026-10-10T14:01:00Z"
            )
        ],
        [unknown],
        [],
    )

    assert by_id(single)["man-0001"]["status"] == "matched"
    assert by_id(single)["man-0001"]["reasons"] == ["time_unknown"]
    assert by_id(ambiguous)["man-0001"]["status"] == "manual_only"
    assert by_id(ambiguous)["man-0001"]["reasons"] == [
        "ambiguous_candidates",
        "no_broker_counterpart",
    ]
    assert by_id(after_assertion)["man-0001"]["status"] == "manual_only"


def test_unknown_time_with_a_different_quantity_is_never_called_a_conflict() -> None:
    unknown = manual("man-0001", time_precision="unknown", executed_at_utc=None, quantity="3")
    records = by_id(reconcile([broker("o1")], [unknown], []))

    assert records["man-0001"]["status"] == "manual_only"
    assert records["brk-o1"]["status"] == "broker_only"


def test_several_statement_fills_sum_to_one_broker_order() -> None:
    order = broker(
        "o1", ordered_at_utc="2026-10-09T13:59:00Z", executed_at_utc="2026-10-09T14:05:00Z"
    )
    fills = [
        imported(
            "imp-0001", 1, quantity="4", price="249.90", executed_at_utc="2026-10-09T14:00:00Z"
        ),
        imported(
            "imp-0002", 2, quantity="6", price="250.10", executed_at_utc="2026-10-09T14:05:00Z"
        ),
    ]
    records = by_id(reconcile([order], [], fills))

    assert records["imp-0001"]["status"] == "matched"
    assert records["imp-0002"]["status"] == "matched"
    assert records["imp-0001"]["reasons"] == ["aggregated_fills"]
    # Average 250.02 against 250.00.
    assert records["brk-o1"]["counterparts"][0]["price_difference_bps"] == "0.8"


def test_order_id_link_decides_regardless_of_time() -> None:
    linked = imported(
        "imp-0001",
        1,
        broker_execution_id="o1",
        quantity="9",
        executed_at_utc="2026-10-12T14:00:00Z",
    )
    records = by_id(reconcile([broker("o1")], [], [linked]))

    assert records["imp-0001"]["status"] == "conflicting"
    assert records["imp-0001"]["reasons"] == ["order_id_link", "quantity_differs"]


def test_ledgers_reconcile_independently_and_per_instrument() -> None:
    # The manual record conflicts while the statement row matches the same order.
    result = reconcile(
        [broker("o1"), broker("o2", instrument_id=MSFT, symbol="MSFT")],
        [manual("man-0001", price="260")],
        [imported("imp-0001", 1)],
    )
    records = by_id(result)

    assert records["brk-o1"]["status"] == "conflicting"
    assert records["imp-0001"]["status"] == "matched"
    assert records["man-0001"]["status"] == "conflicting"
    assert records["brk-o2"]["status"] == "broker_only"


def test_since_filters_the_view_but_not_the_counterparts() -> None:
    result = reconcile(
        [broker("o1", executed_at_utc="2026-10-09T14:00:30Z")],
        [manual("man-0001", executed_at_utc="2026-10-09T13:59:00Z")],
        [],
        since=datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc),
    )

    assert [record["execution_id"] for record in result["records"]] == ["brk-o1"]
    assert result["records"][0]["status"] == "matched"
    assert result["summary"]["matched"] == 1


def test_result_is_deterministic() -> None:
    inputs = (
        [broker("o1"), broker("o2", side="sell", executed_at_utc="2026-10-09T15:00:00Z")],
        [manual("man-0001"), manual("man-0002", quantity="5")],
        [imported("imp-0001", 1), imported("imp-0002", 2, side="sell")],
    )
    first = json.dumps(reconcile(*inputs), sort_keys=True)
    second = json.dumps(reconcile(*(list(reversed(items)) for items in inputs)), sort_keys=True)

    assert first == second


def test_a_record_with_one_fitting_order_is_not_crowded_out() -> None:
    # imp-0001 fits both orders; imp-0002 fits only o1, which sorts first.
    wide = broker(
        "o1", ordered_at_utc="2026-10-09T13:59:00Z", executed_at_utc="2026-10-09T14:10:00Z"
    )
    short = broker(
        "o2", ordered_at_utc="2026-10-09T14:00:00Z", executed_at_utc="2026-10-09T14:00:30Z"
    )
    rows = [
        imported("imp-0001", 1, executed_at_utc="2026-10-09T14:00:15Z"),
        imported("imp-0002", 2, executed_at_utc="2026-10-09T14:08:00Z"),
    ]
    records = by_id(reconcile([wide, short], [], rows))

    assert {record["status"] for record in records.values()} == {"matched"}
    assert records["imp-0002"]["counterparts"][0]["execution_id"] == "brk-o1"
    assert records["imp-0001"]["counterparts"][0]["execution_id"] == "brk-o2"

"""Grouping fills into trades by rule (ADR 0042)."""

from __future__ import annotations

from typing import Any

from services.review.trades import Rule, build_trades, fills_of, trade_document

AAPL = "us:aapl:xnas:2026"


def broker(
    order_id: str,
    side: str,
    quantity: str,
    price: str,
    when: str | None,
    account: str = "kiwoom-a",
    fees: str | None = "1",
    instrument_id: str = AAPL,
    trade_date: str | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "execution_id": f"brk-{order_id}",
        "instrument_id": instrument_id,
        "side": side,
        "quantity": quantity,
        "price": price,
        "fees": fees,
        "executed_at_utc": when,
        "ordered_at_utc": when,
        "broker_account_ref": account,
    }
    if trade_date is not None:
        document["trade_date"] = trade_date
    return document


# Two scale-ins and two partial exits over two days, then a fresh round on day three.
SWING = [
    broker("1", "buy", "10", "100", "2026-03-02T15:00:00Z"),
    broker("2", "buy", "10", "110", "2026-03-02T16:00:00Z"),
    broker("3", "sell", "5", "120", "2026-03-03T15:00:00Z"),
    broker("4", "sell", "15", "130", "2026-03-03T16:00:00Z"),
    broker("5", "buy", "4", "90", "2026-03-04T15:00:00Z"),
    broker("6", "sell", "4", "95", "2026-03-04T16:00:00Z"),
]


def trades(
    documents: list[dict[str, Any]],
    rule: Rule = "flat",
    groups: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    fills, _undated = fills_of("broker", documents)
    assignments = {fill.ref: "strategy-x" for fill in fills}
    built = build_trades(fills, assignments, {"strategy-x": rule}, groups or {})
    return [trade_document(item) for item in built]


def ids(trade: dict[str, Any]) -> list[str]:
    return [execution["execution_id"] for execution in trade["executions"]]


def test_flat_rule_groups_from_flat_back_to_flat() -> None:
    first, second = trades(SWING, "flat")
    assert ids(first) == ["brk-1", "brk-2", "brk-3", "brk-4"]
    assert first["status"] == "closed"
    assert first["entry_quantity"] == "20"
    assert first["average_entry_price"] == "105"
    assert first["average_exit_price"] == "127.5"
    # FIFO: 5@120 and 5@130 close the 100 lot, 10@130 the 110 lot.
    assert first["gross_pnl"] == "450"
    assert first["fees"] == "4"
    assert first["net_pnl"] == "446"
    assert first["holding_days"] == 1
    assert first["trade_id"] == "flat:broker:brk-1"
    assert ids(second) == ["brk-5", "brk-6"]
    assert second["net_pnl"] == "18"


def test_day_rule_groups_each_session_day() -> None:
    day1, day2, day3 = trades(SWING, "day")
    assert ids(day1) == ["brk-1", "brk-2"]
    # Its shares were sold the next day, by the day-2 trade: none are left open.
    assert day1["status"] == "closed"
    assert day1["open_quantity"] == "0"
    assert day1["gross_pnl"] == "0"
    assert ids(day2) == ["brk-3", "brk-4"]
    assert day2["gross_pnl"] == "450"  # the P&L goes to the day of the sells
    assert ids(day3) == ["brk-5", "brk-6"]


def test_fifo_rule_pairs_each_buy_with_the_sells_that_close_it() -> None:
    lot1, lot2, lot3 = trades(SWING, "fifo")
    assert [(e["execution_id"], e["quantity"]) for e in lot1["executions"]] == [
        ("brk-1", "10"),
        ("brk-3", "5"),
        ("brk-4", "5"),
    ]
    assert lot1["gross_pnl"] == "250"
    # The 15-share sell is split: 5 close lot 1, 10 close lot 2; fees follow the split.
    assert [(e["execution_id"], e["quantity"]) for e in lot2["executions"]] == [
        ("brk-2", "10"),
        ("brk-4", "10"),
    ]
    assert lot2["gross_pnl"] == "200"
    assert lot1["fees"] == "2.3333"
    assert lot2["fees"] == "1.6667"
    assert ids(lot3) == ["brk-5", "brk-6"]


def test_manual_rule_uses_the_users_groups_and_leaves_the_rest_alone() -> None:
    groups = {"broker:brk-1": "g1", "broker:brk-2": "g1", "broker:brk-4": "g1"}
    built = trades(SWING, "manual", groups)
    assert [ids(t) for t in built] == [
        ["brk-1", "brk-2", "brk-4"],
        ["brk-3"],
        ["brk-5"],
        ["brk-6"],
    ]
    assert built[0]["trade_id"] == "manual:group-g1"


def test_unassigned_fills_use_flat_and_strategies_split_streams() -> None:
    fills, _ = fills_of("broker", SWING)
    # Only the third round belongs to the strategy: it is its own stream.
    assignments = {"broker:brk-5": "strategy-x", "broker:brk-6": "strategy-x"}
    built = build_trades(fills, assignments, {"strategy-x": "fifo"}, {})
    assert [(t.strategy_id, t.rule) for t in built] == [(None, "flat"), ("strategy-x", "fifo")]


def test_accounts_are_never_mixed() -> None:
    documents = [
        broker("1", "buy", "10", "100", "2026-03-02T15:00:00Z", account="toss-a-1"),
        broker("2", "sell", "10", "110", "2026-03-02T16:00:00Z", account="kiwoom-a"),
    ]
    toss, kiwoom = trades(documents)
    assert toss["status"] == "open"
    assert toss["account_ref"] == "toss-a-1"
    assert kiwoom["status"] == "incomplete"  # a sell with nothing bought in that account
    assert kiwoom["unmatched_quantity"] == "10"
    assert kiwoom["gross_pnl"] == "0"


def test_fills_known_only_by_day_are_flagged_and_put_buys_first() -> None:
    documents = [
        broker("2", "sell", "10", "110", None, trade_date="2026-03-02"),
        broker("1", "buy", "10", "100", None, trade_date="2026-03-02"),
    ]
    (trade,) = trades(documents)
    assert ids(trade) == ["brk-1", "brk-2"]
    assert trade["time_uncertain"] is True
    assert trade["gross_pnl"] == "100"
    assert trade["opened_at_utc"] is None


def test_unreported_fees_are_counted_not_guessed() -> None:
    documents = [
        broker("1", "buy", "10", "100", "2026-03-02T15:00:00Z", fees=None),
        broker("2", "sell", "10", "110", "2026-03-02T16:00:00Z"),
    ]
    (trade,) = trades(documents)
    assert trade["fees_unreported_count"] == 1
    assert trade["fees"] == "1"


def test_records_without_any_date_are_left_out_and_counted() -> None:
    manual = {
        "execution_id": "man-1",
        "instrument_id": AAPL,
        "side": "buy",
        "quantity": "1",
        "price": "1",
        "fees": "0",
        "executed_at_utc": None,
        "time_precision": "unknown",
        "asserted_at_utc": "2026-03-02T00:00:00Z",
    }
    fills, undated = fills_of("manual", [manual])
    assert fills == []
    assert undated == 1


def test_korean_fills_use_the_seoul_session_day() -> None:
    samsung = "kr:005930:xkrx:2026"
    documents = [
        # 2026-03-02 23:30 UTC is 08:30 on 3 March in Seoul.
        broker("1", "buy", "1", "70000", "2026-03-02T23:30:00Z", instrument_id=samsung),
    ]
    (trade,) = trades(documents)
    assert trade["opened_on"] == "2026-03-03"

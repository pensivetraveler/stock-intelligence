"""메리츠증권 Open API: shared session, daily bars, quote, account and realtime (no network).

Payloads follow the official spec's examples and field lists (openapi.imeritz.com JSON,
generated 2026-10-26); they are not captured from a live account.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.journal.store import JournalStore
from services.market_data.broker_common import BlockedEndpointError, BrokerConfigError
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.meritz import (
    MeritzConfig,
    MeritzMarketDataProvider,
    market_quote,
    tick_times,
)
from services.market_data.meritz_account import (
    account_snapshot,
    sync_executions,
    us_session_date,
)
from services.market_data.meritz_session import (
    HISTORY_PATH,
    MeritzCredentials,
    MeritzSession,
    normalize_mac,
)
from services.market_data.meritz_stream import MeritzRealtimeHub
from services.market_data.provider import (
    BarsQuery,
    InvalidBarsQueryError,
    ProviderDataError,
    ProviderRateLimitError,
)

NOW = datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc)  # 06:00 KST on the 17th
MAC = "A1B2C3D4E5F6"
SECRET = "test-secret-not-real"
TOKEN = {
    "access_token": "meritz-token",
    "scope": "login",
    "token_type": "Bearer",
    "expires_in": 604800,
}


class Broker:
    """Answers like the spec: token form POST, then GET queries judged by `rsp_cd`."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        assert request.headers["mac_address"] == MAC
        assert request.headers["authorization"] == "Bearer meritz-token"
        route = self.routes[request.url.path]
        payload = route(request) if callable(route) else route
        status = 200 if str(payload.get("rsp_cd")) in ("0000", "5766", "5762") else 500
        return httpx.Response(status, json=payload)


def session(broker: Broker) -> MeritzSession:
    return MeritzSession(
        MeritzCredentials("meritz-app-key", SECRET, MAC),
        transport=httpx.MockTransport(broker),
        min_interval_seconds=0,
    )


# --- session -------------------------------------------------------------------------


def test_mac_addresses_are_normalized_and_checked() -> None:
    assert normalize_mac("a1:b2:c3:d4:e5:f6") == MAC
    assert normalize_mac("A1-B2-C3-D4-E5-F6") == MAC
    with pytest.raises(BrokerConfigError):
        normalize_mac("not-a-mac")


def test_only_read_only_queries_can_be_sent() -> None:
    meritz = session(Broker({}))
    for path in (
        "/trading/v1/overseas/orders/buy",
        "/trading/v1/overseas/orders/cancel",
        "/forex/v1/exchanges",
    ):
        with pytest.raises(ProviderDataError, match="allowlist"):
            meritz.get(path)
        with pytest.raises(BlockedEndpointError):
            meritz._http.request("POST", path)


def test_pages_follow_the_continuation_key_unchanged_and_no_rows_is_empty() -> None:
    def history(request: httpx.Request) -> dict[str, Any]:
        if request.url.params.get("tr_cont") == "1":
            assert request.url.params["tr_cont_key"] == "  key with   spaces "
            return {"data": [{"oder_no": 2}], "rsp_cd": "5766", "tr_cont": "0"}
        return {
            "data": [{"oder_no": 1}],
            "rsp_cd": "5762",
            "tr_cont": "1",
            "tr_cont_key": "  key with   spaces ",
        }

    meritz = session(Broker({HISTORY_PATH: history}))
    assert [row["oder_no"] for row in meritz.rows(HISTORY_PATH, {"cntg_cls_code": "1"})] == [1, 2]
    empty = session(
        Broker({HISTORY_PATH: {"rsp_cd": "5820", "rsp_msg": "조회할 자료가 없습니다."}})
    )
    assert list(empty.rows(HISTORY_PATH)) == []


def test_errors_are_judged_by_rsp_cd_without_leaking_secrets() -> None:
    limited = session(
        Broker({HISTORY_PATH: {"rsp_cd": "EGW00200", "rsp_msg": "초당 거래건수 초과"}})
    )
    with pytest.raises(ProviderRateLimitError):
        limited.get(HISTORY_PATH)
    refused = session(Broker({HISTORY_PATH: {"rsp_cd": "3174", "rsp_msg": "기간 오류"}}))
    with pytest.raises(InvalidBarsQueryError) as raised:
        refused.get(HISTORY_PATH)
    assert SECRET not in str(raised.value) and MAC not in str(raised.value)
    assert SECRET not in repr(MeritzCredentials("k", SECRET, MAC))


# --- daily bars ------------------------------------------------------------------------


def day_row(day: str, close: str, volume: int) -> dict[str, Any]:
    return {
        "date": day,
        "prpr": close,
        "prdy_vrss_sign": "2",
        "prdy_vrss": "1.0000",
        "prdy_ctrt": "0.30",
        "acml_vol": volume,
        "oprc": "300.0000",
        "hprc": "310.0000",
        "lprc": "299.0000",
        "dcpn_cnt": 4,
    }


def provider(broker: Broker) -> MeritzMarketDataProvider:
    return MeritzMarketDataProvider(
        MeritzConfig(app_key="meritz-app-key", app_secret=SECRET, mac_address=MAC),
        transport=httpx.MockTransport(broker),
        now=lambda: NOW,
    )


def test_daily_bars_ask_for_prices_as_traded_and_skip_days_without_trades() -> None:
    days = {
        "data": [
            day_row("20261016", "305.0000", 0),  # not traded (spec example: volume 0)
            day_row("20261015", "304.0000", 70011913),
            day_row("20261014", "303.0000", 61000000),
        ],
        "rsp_cd": "0000",
    }
    broker = Broker({"/market/v1/overseas/candles/days": days})
    query = BarsQuery(
        "us:aapl:xnas:2026",
        "1d",
        datetime(2026, 10, 14, tzinfo=timezone.utc),
        datetime(2026, 10, 17, tzinfo=timezone.utc),
    )
    result = provider(broker).get_daily_bars(query)
    validate_document(result.to_document())
    request = broker.requests[-1]
    assert dict(request.url.params) == {
        "mrkt_div_code": "OV",
        "mrkt_cls_code": "OQ",
        "iscd": "AAPL",
        "dely_rltm_cls_code": "0",
        "mod_stpr_cls_code": "1",
        "from": "20261014",
        "to": "20261016",
    }
    assert [bar["close"] for bar in result.bars] == ["303.0000", "304.0000"]
    assert result.coverage["adjustment"] == "raw"
    missing = [gap["reason"] for gap in result.coverage["missing_intervals"]]
    assert missing == ["provider_no_bar"]

    adjusted = provider(broker).get_adjusted_daily_bars(query, "split_adjusted")
    assert broker.requests[-1].url.params["mod_stpr_cls_code"] == "0"
    assert adjusted.coverage["adjustment"] == "split_adjusted"
    with pytest.raises(InvalidBarsQueryError):
        provider(broker).get_adjusted_daily_bars(query, "fully_adjusted")


# --- quote ---------------------------------------------------------------------------


def test_tick_times_walk_back_across_midnight() -> None:
    times = tick_times(["055959", "000010", "235950", "bad"], NOW)
    assert [t.isoformat() if t else None for t in times] == [
        "2026-10-17T05:59:59+09:00",
        "2026-10-17T00:00:10+09:00",
        "2026-10-16T23:59:50+09:00",
        None,
    ]


def test_quote_takes_the_last_price_from_the_newest_tick() -> None:
    book: dict[str, Any] = {"prdy_clpr": "326.5700", "prpr": "326.3100"}
    for level in range(1, 11):
        book[f"askp{level}"] = "326.4000" if level == 1 else "0.0000"
        book[f"bidp{level}"] = "326.3000" if level == 1 else "0.0000"
        book[f"askp_rsqn{level}"] = 300 if level == 1 else 0
        book[f"bidp_rsqn{level}"] = 200 if level == 1 else 0
    broker = Broker(
        {
            "/market/v1/overseas/prices": {
                "data": {"iscd": "AAPL", "prpr": "326.3100", "crnc_code": "USD"},
                "rsp_cd": "0000",
            },
            "/market/v1/overseas/trades/ticks": {
                "data": [
                    {"hour": "165959", "prpr": "326.3100", "cntg_vol": 10, "korea_hour": "055959"},
                    {"hour": "165950", "prpr": "326.2900", "cntg_vol": 9, "korea_hour": "055950"},
                ],
                "rsp_cd": "0000",
            },
            "/market/v1/overseas/orderbook": {"data": book, "rsp_cd": "0000"},
        }
    )
    quote = market_quote(session(broker), "us:aapl:xnas:2026", lambda: NOW)
    validate_document(quote)
    assert quote["source_id"] == "meritz-us"
    assert quote["last"] == {"price": "326.3100", "at_utc": "2026-10-16T20:59:59.000Z"}
    assert quote["orderbook"]["asks"] == [{"price": "326.4000", "volume": "300"}]
    assert quote["orderbook"]["bids"] == [{"price": "326.3000", "volume": "200"}]
    assert quote["price_limits"] is None

    unknown = Broker(
        {"/market/v1/overseas/prices": {"data": {"iscd": "", "prpr": "0"}, "rsp_cd": "0000"}}
    )
    with pytest.raises(InvalidBarsQueryError):
        market_quote(session(unknown), "us:aapl:xnas:2026", lambda: NOW)


# --- order history -------------------------------------------------------------------


def order(
    number: int, symbol: str, side: str, qty: str, price: str, filled: str, **extra: Any
) -> dict[str, Any]:
    return {
        "oder_date": filled[:10].replace("-", ""),
        "oder_no": number,
        "orgl_oder_no": 0,
        "iscd": f"{symbol}.OQ",
        "kor_isnm": "애플",
        "seln_byov_clcd": side,
        "oder_dqty": f"{qty}.0000000000",
        "ovrs_stck_oder_prc": price,
        "cntg_dqty": f"{qty}.0000000000",
        "cntg_crnc_prc": price,
        "ncnt_odqt": 0,
        "cntg_camt": "0",
        "ovrs_stck_oder_stat_cls_code": "0",
        "oder_rctf_cncl_cls_code": "0",
        "rctf_cncl_cls_name": "",
        "oder_exec_ymdh": filled,
        "cntg_ymdh": filled,
        "aspr_type_code": "2",
        "oder_cond_cls_code": "8",
        "ovrs_stck_mrkt_tr_cls_code": "01",
        "crcd": "USD",
        **extra,
    }


HISTORY = {
    "data": [
        # 23:00 KST = 10:00 New York (regular session).
        order(7, "AAPL", "2", "10", "250.10000000", "2026-10-16 23:00:00"),
        # After midnight in Seoul, still the afternoon of the 16th in New York.
        order(8, "AAPL", "1", "4", "251.00000000", "2026-10-17 04:30:00"),
        # 12:00 KST = 23:00 New York the evening before: the day market of the 16th.
        order(9, "AAPL", "2", "1", "249.00000000", "2026-10-16 12:00:00"),
        # A correction row is an order of its own.
        order(
            10,
            "AAPL",
            "2",
            "2",
            "250.00000000",
            "2026-10-16 23:10:00",
            oder_rctf_cncl_cls_code="1",
            orgl_oder_no=7,
        ),
        # Saturday morning in New York: no session to place it in.
        order(11, "AAPL", "2", "1", "250.00000000", "2026-10-17 18:00:00"),
        order(12, "ZZZZ", "2", "1", "1.00000000", "2026-10-16 23:00:00"),
        order(13, "AAPL", "2", "1", "1.00000000", "2026-10-16 23:00:00", crcd="HKD"),
        order(14, "AAPL", "2", "0", "0.00000000", "2026-10-16 23:00:00", cntg_ymdh=""),
    ],
    "rsp_cd": "5766",
    "tr_cont": "0",
}


def instrument_for(symbol: str, currency: str = "USD", on: object = None) -> str | None:
    return "us:aapl:xnas:2026" if symbol == "AAPL" and currency == "USD" else None


def test_session_dates_for_korean_fill_times() -> None:
    seoul = timezone.utc  # times below are given in UTC
    assert us_session_date(datetime(2026, 10, 16, 14, 0, tzinfo=seoul)) == date(2026, 10, 16)
    assert us_session_date(datetime(2026, 10, 16, 3, 0, tzinfo=seoul)) == date(2026, 10, 16)
    assert us_session_date(datetime(2026, 10, 17, 9, 0, tzinfo=seoul)) is None


def test_sync_stores_each_filled_order_once_without_inventing_fees(tmp_path: Path) -> None:
    broker = Broker({HISTORY_PATH: HISTORY})
    store = JournalStore(tmp_path)
    result = sync_executions(session(broker), store, instrument_for, lambda: NOW, date(2026, 10, 1))
    validate_document(result)
    assert result["broker_id"] == "meritz"
    assert (result["orders_seen"], result["stored_new"], result["already_stored"]) == (8, 4, 0)
    assert result["skipped"] == {
        "not_filled": 1,
        "unsupported_currency": 1,
        "unknown_instrument": 1,
        "time_unresolved": 1,
    }
    assert result["unknown_symbols"] == [{"symbol": "ZZZZ", "orders": 1}]
    params = dict(broker.requests[-1].url.params)
    assert params == {
        "oder_sta_date": "20261001",
        "oder_fin_date": "20261017",  # today in Seoul
        "seln_byov_clcd": "0",
        "cntg_cls_code": "1",
    }

    executions = store.broker_executions()
    assert [e["executed_at_utc"] for e in executions] == [
        "2026-10-16T03:00:00Z",
        "2026-10-16T14:00:00Z",
        "2026-10-16T14:10:00Z",
        "2026-10-16T19:30:00Z",
    ]
    first = executions[1]
    assert (first["side"], first["quantity"], first["price"]) == ("buy", "10", "250.1")
    assert (first["fees"], first["commission"], first["tax"]) == (None, None, None)
    assert (first["time_meaning"], first["order_type"]) == ("reported_fill_time", "LIMIT")
    assert first["broker_order_id"] == "20261016-7"
    assert executions[2]["order_status"] == "ACCEPTED_AMENDED"
    assert first["broker_account_ref"].startswith("meritz-")
    assert "meritz-app-key" not in json.dumps(executions) and MAC not in json.dumps(executions)

    again = sync_executions(session(broker), store, instrument_for, lambda: NOW, date(2026, 10, 1))
    assert (again["stored_new"], again["already_stored"]) == (0, 4)
    with pytest.raises(InvalidBarsQueryError):
        sync_executions(session(broker), store, instrument_for, lambda: NOW, date(2026, 11, 1))


# --- account snapshot ----------------------------------------------------------------

HOLDING = {
    "prod_cls_name": "일반",
    "rmnd_clcd": "1",
    "ovrs_stck_mrkt_tr_cls_code": "01",
    "crcd": "USD",
    "iscd": "AAPL.OQ",
    "isnm": "애플",
    "rmnd_dqty": "10.0000000000",
    "seln_able_dqty": "10.0000000000",
    "avrg_unpr": "247.108400",
    "vltn_prls_camt": "624.9200",
    "vltn_ert": "25.2900",
    "psnt_prc": "309.600000",
    "byov_camt": "2471.0840",
    "frcr_vltn_amt": "3096.000",
    "exrt": "1383.6000",
    "ovrs_stck_symb_iscd": "AAPL",
    "ovrs_exch_vndr_idno": "0537",
}


def test_account_snapshot_reads_holdings_cash_and_open_orders() -> None:
    broker = Broker(
        {
            "/accounts/v1/me/overseas/holdings": {
                "data": [HOLDING],
                "rsp_cd": "5766",
                "tr_cont": "0",
            },
            "/accounts/v1/me/overseas/currencies": {
                "data": [{"crcd": "USD", "frcr_amt": "13233.190", "estm_frcr_amt": "13233.190"}],
                "rsp_cd": "5766",
            },
            "/accounts/v1/me/valuation": {
                "data": {"byng_amt": 211968435, "aset_vltn_amt": 1904460915},
                "rsp_cd": "5766",
            },
            "/accounts/v1/me/deposit": {"rsp_cd": "9999", "rsp_msg": "점검 중"},
            "/trading/v1/overseas/orders/today": {
                "data": [
                    {
                        "oder_date": "20261016",
                        "oder_no": 718028,
                        "iscd": "TSLA.OQ",
                        "ovrs_stck_symb_iscd": "TSLA",
                        "seln_byov_clcd": "2",
                        "aspr_type_code": "2",
                        "odqt": 2,
                        "cntg_qty": 1,
                        "ovrs_stck_oder_prc": "350.58000000",
                        "oder_rctf_cncl_cls_code": "0",
                        "ovrs_stck_oder_stat_cls_code": "0",
                        "oder_exec_ymdh": "2026-10-16 23:48:23",
                    }
                ],
                "rsp_cd": "5766",
                "tr_cont": "0",
            },
        }
    )
    snapshot = account_snapshot(session(broker), lambda: NOW)
    validate_document(snapshot)
    (account,) = snapshot["accounts"]
    assert snapshot["broker_id"] == "meritz"
    assert account["account_suffix"] is None
    (holding,) = account["holdings"]
    assert (holding["symbol"], holding["quantity"], holding["last_price"]) == (
        "AAPL",
        "10",
        "309.6",
    )
    assert account["buying_power"] == {"usd": "13233.19", "krw": None}
    assert account["summary"]["market_value"] == {"krw": "1904460915", "usd": "3096"}
    (open_order,) = account["open_orders"]
    assert (open_order["symbol"], open_order["side"], open_order["filled_quantity"]) == (
        "TSLA",
        "buy",
        "1",
    )
    # The failed part is named; the rest is still shown.
    assert account["errors"] and account["errors"][0].startswith("cash:")


# --- realtime -------------------------------------------------------------------------


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.incoming: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        return await self.incoming.get()

    async def close(self) -> None:
        pass


def test_hub_subscribes_per_venue_and_relays_trades_and_books(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.market_data import meritz_stream

    monkeypatch.setattr(meritz_stream, "SUBSCRIBE_INTERVAL", 0.0)
    connection = FakeConnection()
    broker = Broker({})

    async def connect(url: str, headers: dict[str, str]) -> FakeConnection:
        assert url.startswith("wss://openapi.imeritz.com:29443")
        return connection

    async def scenario() -> list[dict[str, Any]]:
        hub = MeritzRealtimeHub(
            lambda: session(broker),
            ticks=lambda: None,
            instrument_for=instrument_for,
            connect=connect,
            now=lambda: NOW,
        )
        client = hub.add_client(frozenset({"AAPL", "ZZZZ"}))
        for _ in range(50):
            await asyncio.sleep(0)
            if len(connection.sent) >= 4:
                break
        trade = {
            "header": {"tr_cd": "ohhts_exst_cntg", "tr_key": "OQ@AAPL"},
            "body": {
                "iscd": "OQ@AAPL",
                "korea_date": "20261016",
                "korea_hour": "233000",
                "prpr": "250.1000",
                "cnqn": "5",
            },
        }
        book = {
            "header": {"tr_cd": "ohhts_exst_aspr", "tr_key": "OQ@AAPL"},
            "body": {
                "korea_date": "20261016",
                "korea_hour": "233001",
                "askp1": "250.2000",
                "seln_aspr_rsqn1": "100",
                "bidp1": "250.0000",
                "shnu_aspr_rsqn1": "80",
                "askp2": "0",
                "seln_aspr_rsqn2": "0",
            },
        }
        await connection.incoming.put(json.dumps(trade))
        await connection.incoming.put(json.dumps(book))
        events: list[dict[str, Any]] = []
        while len([e for e in events if e["event"] in ("trade", "orderbook")]) < 2:
            events.append(await asyncio.wait_for(client.queue.get(), 1))
        hub.remove_client(client)
        await connection.incoming.put("{}")
        await asyncio.wait_for(hub.stopped(), 1)
        return events

    events = asyncio.run(scenario())
    subscriptions = [json.loads(message) for message in connection.sent]
    assert {(m["body"]["tr_cd"], m["body"]["tr_key"]) for m in subscriptions} == {
        ("ohhts_exst_cntg", "OQ@AAPL"),
        ("ohhts_exst_cnta", "OQ@AAPL"),
        ("ohhts_exst_cntd", "OQ@AAPL"),
        ("ohhts_exst_aspr", "OQ@AAPL"),
    }
    assert {m["header"]["token"] for m in subscriptions} == {"Bearer meritz-token"}
    status = [e for e in events if e["event"] == "status" and e["connected"]]
    assert status and status[-1]["rejected"] == ["ZZZZ"]
    trade = next(e for e in events if e["event"] == "trade")
    validate_document(trade)
    assert (trade["source_id"], trade["price"], trade["exchange_time_utc"]) == (
        "meritz-us",
        "250.1000",
        "2026-10-16T14:30:00.000Z",
    )
    book = next(e for e in events if e["event"] == "orderbook")
    validate_document(book)
    assert book["asks"] == [{"price": "250.2000", "volume": "100"}]


# --- API ------------------------------------------------------------------------------


def test_meritz_endpoints(tmp_path: Path) -> None:
    broker = Broker(
        {
            HISTORY_PATH: HISTORY,
            "/accounts/v1/me/overseas/holdings": {"rsp_cd": "5820"},
            "/accounts/v1/me/overseas/currencies": {"rsp_cd": "5820"},
            "/accounts/v1/me/valuation": {"rsp_cd": "5820"},
            "/accounts/v1/me/deposit": {"rsp_cd": "5820"},
            "/trading/v1/overseas/orders/today": {"rsp_cd": "5820"},
        }
    )
    meritz = session(broker)
    app = create_app(
        FakeMarketDataProvider.from_path(),
        journal_store=JournalStore(tmp_path),
        meritz_session=lambda: meritz,
        toss_session=lambda: None,
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        synced = client.post("/v1/brokers/meritz/sync", params={"since": "2026-10-01"})
        assert synced.status_code == 200, synced.text
        assert synced.json()["stored_new"] == 4
        ledger = client.get("/v1/trades/broker").json()
        validate_document(ledger)
        assert {item["broker_id"] for item in ledger["executions"]} == {"meritz"}
        account = client.get("/v1/brokers/meritz/account")
        assert account.status_code == 200, account.text
        assert account.json()["accounts"][0]["holdings"] == []
    missing = create_app(
        FakeMarketDataProvider.from_path(),
        journal_store=JournalStore(tmp_path),
        meritz_session=lambda: None,
        toss_session=lambda: None,
        clock=lambda: NOW,
    )
    with TestClient(missing) as other:
        refused = other.get("/v1/brokers/meritz/account")
        assert refused.status_code == 404
        assert "Meritz" in refused.json()["error"]["message"]
        # Without a Toss key a US quote falls back to Meritz, which is missing too.
        quote = other.get("/v1/market/quote", params={"instrument_id": "us:aapl:xnas:2026"})
        assert quote.status_code == 404
    # Nothing but the token and GET queries was sent.
    assert {r.method for r in broker.requests} == {"POST", "GET"}
    assert {r.url.path for r in broker.requests if r.method == "POST"} == {"/oauth2/token"}

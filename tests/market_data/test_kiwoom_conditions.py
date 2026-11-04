"""Kiwoom US condition search over a fake WebSocket (no network, ADR 0048).

Replies follow the shapes seen live on 2026-11-04: `[seq, name]` pairs from GCNSRLST and
zero-padded, signed numbers from GCNSRREQ. SIW_당일급등주 is left out of the saved list so
a connected condition that is not saved can be tested.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.kiwoom_conditions import (
    DEMO_URL,
    MAX_PAGES,
    REAL_URL,
    ConditionNotFoundError,
    KiwoomConditionSearch,
)
from services.market_data.provider import (
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

NOW = datetime(2026, 11, 4, 1, 30, tzinfo=timezone.utc)
CONDITIONS = [["1", "SIW_전일신고거래량"], ["3", "SIW_전일급등주"], ["46", "정규장거래량"]]


def row(symbol: str, last: str = "000005.1600", change: str = "000000.9400") -> dict[str, str]:
    return {
        "10": last,
        "11": change,
        "12": "00022.27",
        "13": "0009011562",
        "16": "000004.7100",
        "17": "000005.2000",
        "18": "000004.7100",
        "25": "2",
        "302": "오로라 캐너비스",
        "318": "",
        "9001": symbol,
        "stex_tp": "ND",
    }


class Server:
    """Kiwoom's side of one connection: answers each message the client sends."""

    def __init__(
        self,
        *,
        login_codes: list[int] | None = None,
        pages: list[list[dict[str, str]]] | None = None,
        search_code: int = 0,
        list_name: str = "GCNSRLST",
        ping_first: bool = False,
        conditions: list[list[str]] | None = None,
    ) -> None:
        self.conditions = conditions if conditions is not None else CONDITIONS
        self.login_codes = login_codes or [0]
        self.pages = pages if pages is not None else [[row("ACB")]]
        self.search_code = search_code
        self.list_name = list_name
        self.ping_first = ping_first
        self.urls: list[str] = []
        self.sent: list[dict[str, Any]] = []
        self.closed = 0

    def connect(self, url: str) -> Connection:
        self.urls.append(url)
        return Connection(self)


class Connection:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.outbox: list[str] = ["PING"] if server.ping_first else []

    def send(self, message: str) -> None:
        body = {"trnm": "PING"} if message == "PING" else json.loads(message)
        self.server.sent.append(body)
        name = body["trnm"]
        if name == "LOGIN":
            code = self.server.login_codes.pop(0) if self.server.login_codes else 0
            reply: dict[str, Any] = {"trnm": "LOGIN", "return_code": code, "return_msg": "x"}
            self.outbox.append(json.dumps({"trnm": "PING"}))
        elif name == "GCNSRLST":
            reply = {
                "trnm": self.server.list_name,
                "return_code": 0,
                "data": self.server.conditions,
            }
        elif name == "GCNSRREQ":
            page = int(body["next_key"] or "0")
            more = page + 1 < len(self.server.pages)
            reply = {
                "trnm": "GCNSRREQ",
                "seq": body["seq"],
                "return_code": self.server.search_code,
                "return_msg": "refused",
                "cont_yn": "Y" if more else "N",
                "next_key": str(page + 1) if more else "",
                "data": self.server.pages[page] if self.server.pages else [],
            }
        else:  # a PING echo
            return
        self.outbox.append(json.dumps(reply, ensure_ascii=False))

    def recv(self, timeout: float | None = None) -> str:
        if not self.outbox:
            raise TimeoutError
        return self.outbox.pop(0)

    def close(self) -> None:
        self.server.closed += 1


def search(server: Server, tokens: list[bool] | None = None) -> KiwoomConditionSearch:
    def token(refresh: bool) -> str:
        if tokens is not None:
            tokens.append(refresh)
        return "fresh" if refresh else "cached"

    return KiwoomConditionSearch(token, connect=server.connect)


def test_conditions_are_listed_after_login() -> None:
    server = Server(ping_first=True)
    found = search(server).conditions()
    assert [(item.seq, item.name) for item in found] == [
        ("1", "SIW_전일신고거래량"),
        ("3", "SIW_전일급등주"),
        ("46", "정규장거래량"),
    ]
    assert server.urls == [REAL_URL]
    assert server.sent[0] == {"trnm": "LOGIN", "token": "cached"}
    assert {"trnm": "PING"} in server.sent  # every PING is echoed
    assert server.closed == 1


def test_the_spec_spelling_of_the_list_reply_is_accepted() -> None:
    assert len(search(Server(list_name="GCNSRLT")).conditions()) == len(CONDITIONS)


def test_a_run_reads_every_page_as_exact_decimals() -> None:
    server = Server(pages=[[row("ACB")], [row("GPUS", "000000.1602", "-00000.0265")]])
    run = search(server).run("정규장거래량")
    assert run.condition.seq == "46"
    assert run.complete
    assert [item.symbol for item in run.rows] == ["ACB", "GPUS"]
    first, second = run.rows
    assert first.numbers == {
        "last": "5.1600",
        "change": "0.9400",
        "change_percent": "22.27",
        "volume": "9011562",
        "open": "4.7100",
        "high": "5.2000",
        "low": "4.7100",
    }
    assert first.sector is None and first.kiwoom_exchange == "ND"
    assert second.numbers["change"] == "-0.0265"
    requests = [body for body in server.sent if body["trnm"] == "GCNSRREQ"]
    assert [(body["cont_yn"], body["next_key"]) for body in requests] == [("N", ""), ("Y", "1")]
    assert all(body["search_type"] == "0" for body in requests)
    # The list is asked first on the same connection.
    names = [body["trnm"] for body in server.sent if body["trnm"] != "PING"]
    assert names == ["LOGIN", "GCNSRLST", "GCNSRREQ", "GCNSRREQ"]


def test_a_run_stops_at_the_page_limit() -> None:
    run = search(Server(pages=[[row("ACB")]] * (MAX_PAGES + 3))).run("정규장거래량")
    assert not run.complete
    assert len(run.rows) == MAX_PAGES


def test_an_unsaved_or_ambiguous_name_is_refused_before_any_search() -> None:
    server = Server()
    with pytest.raises(ConditionNotFoundError):
        search(server).run("SIW_당일급등주")
    assert all(body["trnm"] != "GCNSRREQ" for body in server.sent)
    assert server.closed == 1
    twice = Server(conditions=[["1", "정규장거래량"], ["2", "정규장거래량"]])
    with pytest.raises(InvalidBarsQueryError):
        search(twice).run("정규장거래량")
    assert all(body["trnm"] != "GCNSRREQ" for body in twice.sent)


def test_the_seq_is_looked_up_by_name_on_every_run() -> None:
    # Kiwoom renumbers conditions when one is added above them.
    server = Server(conditions=[["42", "정규장거래량"]])
    assert search(server).run("정규장거래량").condition.seq == "42"
    server.conditions = [["1", "SIW_전일신고거래량"], ["46", "정규장거래량"]]
    assert search(server).run("정규장거래량").condition.seq == "46"
    requests = [body["seq"] for body in server.sent if body["trnm"] == "GCNSRREQ"]
    assert requests == ["42", "46"]


def test_a_refused_login_is_retried_once_with_a_fresh_token() -> None:
    tokens: list[bool] = []
    server = Server(login_codes=[8005, 0])
    assert len(search(server, tokens).conditions()) == len(CONDITIONS)
    assert tokens == [False, True]
    assert server.closed == 2

    with pytest.raises(ProviderAuthError):
        search(Server(login_codes=[8005, 8005])).conditions()
    with pytest.raises(ProviderRateLimitError):
        search(Server(login_codes=[1700])).conditions()


def test_refusals_and_bad_rows_fail_loudly() -> None:
    with pytest.raises(ProviderRateLimitError):
        search(Server(search_code=1701)).run("정규장거래량")
    with pytest.raises(InvalidBarsQueryError):
        search(Server(search_code=20)).run("정규장거래량")
    with pytest.raises(ProviderDataError):
        search(Server(pages=[[{**row("ACB"), "10": "n/a"}]])).run("정규장거래량")
    with pytest.raises(ProviderDataError):
        search(Server(pages=[[{**row("ACB"), "9001": " "}]])).run("정규장거래량")


def test_silence_is_unavailable() -> None:
    class Mute(Connection):
        def send(self, message: str) -> None:
            self.server.sent.append(json.loads(message))

    server = Server()
    client = KiwoomConditionSearch(lambda _refresh: "t", connect=lambda url: Mute(server))
    with pytest.raises(ProviderUnavailableError):
        client.conditions()


def test_demo_keys_use_the_demo_domain() -> None:
    server = Server()
    KiwoomConditionSearch(lambda _r: "t", "demo", connect=server.connect).conditions()
    assert server.urls == [DEMO_URL]


class Session:
    environment = "real"

    def access_token(self, refresh: bool = False) -> str:
        return "token"


def test_condition_api() -> None:
    server = Server(pages=[[row("AAPL"), row("ZZZZQ")]])
    app = create_app(
        FakeMarketDataProvider.from_path(),
        kiwoom_session=lambda: Session(),  # type: ignore[arg-type, return-value]
        kiwoom_conditions_connect=server.connect,
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        listed = client.get("/v1/screeners/kiwoom/us/conditions")
        assert listed.status_code == 200, listed.text
        validate_document(listed.json())
        # Only the connected conditions, with their current numbers; an unsaved one is named.
        assert listed.json()["conditions"] == [
            {"seq": "1", "name": "SIW_전일신고거래량"},
            {"seq": "3", "name": "SIW_전일급등주"},
        ]
        assert listed.json()["missing"] == ["SIW_당일급등주"]
        assert listed.headers["cache-control"] == "no-store"

        ran = client.post(
            "/v1/screeners/kiwoom/us/conditions/run", params={"name": "SIW_전일급등주"}
        )
        assert ran.status_code == 200, ran.text
        document = ran.json()
        validate_document(document)
        assert document["fetched_at_utc"] == "2026-11-04T01:30:00Z"
        assert document["condition"] == {"seq": "3", "name": "SIW_전일급등주"}
        aapl, unknown = document["rows"]
        # A known ticker gets the app's own listing; an unknown one is not chartable.
        assert aapl["listing"]["instrument"]["instrument_id"] == "us:aapl:xnas:2026"
        assert unknown["listing"] is None
        assert aapl["last"] == "5.1600" and aapl["kiwoom_exchange"] == "ND"

        def run(name: str) -> tuple[int, str]:
            reply = client.post("/v1/screeners/kiwoom/us/conditions/run", params={"name": name})
            return reply.status_code, reply.json().get("error", {}).get("code", "")

        assert run("SIW_당일급등주") == (404, "condition_not_found")
        searches = sum(body["trnm"] == "GCNSRREQ" for body in server.sent)
        # A saved condition that is not connected is never sent to Kiwoom.
        assert run("정규장거래량") == (422, "condition_not_connected")
        assert sum(body["trnm"] == "GCNSRREQ" for body in server.sent) == searches

    missing = create_app(FakeMarketDataProvider.from_path(), kiwoom_session=lambda: None)
    with TestClient(missing) as other:
        assert other.get("/v1/screeners/kiwoom/us/conditions").status_code == 404

"""ECOS USD/KRW client against a fake server (shapes from the ECOS guide, not a live key)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from services.market_data.ecos_fx import EcosError, EcosUsdKrw

KEY = "ecos-test-key-not-real"
NOW = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)  # 10:00 Seoul, Friday


def _client(reply: httpx.Response | Exception, seen: list[httpx.Request]) -> EcosUsdKrw:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return EcosUsdKrw(KEY, transport=httpx.MockTransport(handler), now=lambda: NOW)


def _rows(*pairs: tuple[str, str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "StatisticSearch": {
                "list_total_count": len(pairs),
                "row": [
                    {"STAT_CODE": "731Y001", "ITEM_CODE1": "0000001", "TIME": t, "DATA_VALUE": v}
                    for t, v in pairs
                ],
            }
        },
    )


def test_latest_rate_before_today_in_seoul() -> None:
    seen: list[httpx.Request] = []
    # 2026-10-23 itself is today in Seoul: not used even if ECOS already lists it.
    client = _client(
        _rows(("20261021", "1,380.5"), ("20261022", "1385.2"), ("20261023", "1400")), seen
    )
    rate = client.rate()
    assert (rate.krw_per_usd, rate.observation_date) == (Decimal("1385.2"), date(2026, 10, 22))
    assert "731Y001/0000001" in rate.source
    path = seen[0].url.path
    assert path.startswith(f"/api/StatisticSearch/{KEY}/json/kr/1/15/731Y001/D/")
    assert path.endswith("/20261008/20261022/0000001")
    client.rate()
    assert len(seen) == 1  # cached for the Seoul day


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"RESULT": {"CODE": "INFO-200", "MESSAGE": "없음"}}),
        httpx.Response(200, json={"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키 오류"}}),
        httpx.Response(500, text="error"),
        httpx.Response(200, text="<html>"),
        httpx.ConnectError("refused"),
        _rows(("20261023", "1400")),
    ],
)
def test_failures_never_show_the_key(reply: httpx.Response | Exception) -> None:
    with pytest.raises(EcosError) as raised:
        _client(reply, []).rate()
    assert KEY not in str(raised.value)
    assert "ecos.bok.or.kr" not in str(raised.value)


def test_repr_hides_the_key() -> None:
    assert KEY not in repr(EcosUsdKrw(KEY))


class Clock:
    def __init__(self) -> None:
        self.at = NOW

    def __call__(self) -> datetime:
        return self.at


def _error(code: str) -> httpx.Response:
    return httpx.Response(200, json={"RESULT": {"CODE": code, "MESSAGE": "메시지"}})


@pytest.mark.parametrize(
    ("code", "permanent"),
    [
        ("INFO-100", True),  # invalid key
        ("ERROR-101", True),  # malformed date
        ("ERROR-400", False),  # search timeout
        ("ERROR-500", False),  # server error
        ("ERROR-602", False),  # too many calls
    ],
)
def test_error_codes_are_classified(code: str, permanent: bool) -> None:
    with pytest.raises(EcosError) as raised:
        _client(_error(code), []).rate()
    assert (raised.value.code, raised.value.permanent) == (code, permanent)
    assert code in str(raised.value)


def test_result_inside_the_service_is_read_too() -> None:
    reply = httpx.Response(
        200, json={"StatisticSearch": {"RESULT": {"CODE": "ERROR-602", "MESSAGE": "제한"}}}
    )
    with pytest.raises(EcosError) as raised:
        _client(reply, []).rate()
    assert raised.value.code == "ERROR-602"


@pytest.mark.parametrize(("code", "wait_minutes"), [("ERROR-500", 5), ("INFO-100", 60)])
def test_a_failure_is_not_retried_before_its_wait(code: str, wait_minutes: int) -> None:
    seen: list[httpx.Request] = []
    replies = [_error(code), _rows(("20261022", "1385.2"))]
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies[min(len(seen), len(replies)) - 1]

    client = EcosUsdKrw(KEY, transport=httpx.MockTransport(handler), now=clock)
    for _ in range(3):
        with pytest.raises(EcosError):
            client.rate()
    assert len(seen) == 1
    clock.at = NOW + timedelta(minutes=wait_minutes, seconds=1)
    assert client.rate().krw_per_usd == Decimal("1385.2")
    assert len(seen) == 2

"""ECOS USD/KRW client against a fake server (shapes from the ECOS guide, not a live key)."""

from __future__ import annotations

from datetime import date, datetime, timezone
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

"""KIS, Toss and Kiwoom adapters against responses shaped like their official specs.

Payloads follow the official docs (KIS open-trading-api samples, Toss OpenAPI markdown,
Kiwoom kiwoom_api_spec.json); they are not captured from live accounts. No network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from packages.contracts.validator import validate_document
from services.credentials.catalog import SECRET_ENV_NAMES
from services.credentials.store import CredentialStore, MemorySecretBackend
from services.market_data.broker_common import AllowlistedClient, BlockedEndpointError
from services.market_data.kis import KisConfig, KisMarketDataProvider
from services.market_data.kiwoom import KiwoomConfig, KiwoomMarketDataProvider
from services.market_data.meritz import MeritzConfig, MeritzMarketDataProvider
from services.market_data.provider import (
    BarsQuery,
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
)
from services.market_data.selection import ProviderConfigError, provider_from_env
from services.market_data.toss import TossConfig, TossMarketDataProvider

NOW = datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc)  # after the 2026-10-16 close
SPY = "us:spy:arcx:2026"
SECRET = "test-secret-not-real"


class Recorder:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def j(payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        text=json.dumps(payload),
        headers={"Content-Type": "application/json", **(headers or {})},
    )


def window(start: str, end: str) -> BarsQuery:
    day = "2026-10-16T"
    return BarsQuery(
        SPY,
        "1m",
        datetime.fromisoformat(f"{day}{start}:00+00:00"),
        datetime.fromisoformat(f"{day}{end}:00+00:00"),
    )


# --- KIS -------------------------------------------------------------------------------


def kis_record(local_hms: str, last: str = "773.2500") -> dict[str, str]:
    return {
        "tymd": "20261016",
        "xymd": "20261016",
        "xhms": local_hms,
        "kymd": "20261016",
        "khms": "223000",
        "open": "773.2400",
        "high": "773.2600",
        "low": "772.5000",
        "last": last,
        "evol": "4100",
        "eamt": "0",
    }


def kis(recorder: Recorder, **changes: Any) -> KisMarketDataProvider:
    config = KisConfig(app_key="kis-app-key", app_secret=SECRET, **changes)
    return KisMarketDataProvider(config, transport=httpx.MockTransport(recorder), now=lambda: NOW)


KIS_TOKEN = j({"access_token": "kis-token", "token_type": "Bearer", "expires_in": 86400})


def kis_ok(*records: dict[str, str]) -> httpx.Response:
    return j(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
            "output1": {},
            "output2": list(records),
        }
    )


def test_kis_request_follows_the_official_minute_bar_spec() -> None:
    recorder = Recorder(KIS_TOKEN, kis_ok(kis_record("093100"), kis_record("093000")))

    result = kis(recorder).get_bars(window("13:30", "13:32"))

    token, bars = recorder.requests
    assert token.url.path == "/oauth2/tokenP"
    assert json.loads(token.content) == {
        "grant_type": "client_credentials",
        "appkey": "kis-app-key",
        "appsecret": SECRET,
    }
    assert bars.url.path == "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
    assert (bars.headers["tr_id"], bars.headers["custtype"], bars.headers["authorization"]) == (
        "HHDFS76950200",
        "P",
        "Bearer kis-token",
    )
    assert dict(bars.url.params) == {
        "AUTH": "",
        "EXCD": "AMS",
        "SYMB": "SPY",
        "NMIN": "1",
        "PINC": "1",
        "NEXT": "1",
        "NREC": "120",
        "FILL": "",
        "KEYB": "20261016093100",
    }
    validate_document(result.to_document())
    assert [bar["interval_start_utc"] for bar in result.bars] == [
        "2026-10-16T13:30:00Z",
        "2026-10-16T13:31:00Z",
    ]
    assert result.bars[0]["open"] == "773.2400"
    assert result.coverage["adjustment"] == "unknown"


def test_kis_pages_back_with_keyb_and_reissues_an_expired_token_once() -> None:
    first_page = [
        kis_record(f"{9 + (30 + m) // 60:02d}{(30 + m) % 60:02d}00") for m in range(239, 119, -1)
    ]
    recorder = Recorder(
        KIS_TOKEN,
        j({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}, status=500),
        KIS_TOKEN,
        kis_ok(*first_page),
        kis_ok(
            *[
                kis_record(f"{9 + (30 + m) // 60:02d}{(30 + m) % 60:02d}00")
                for m in range(119, -1, -1)
            ]
        ),
    )

    result = kis(recorder).get_bars(window("13:30", "17:30"))

    assert [r.url.path for r in recorder.requests].count("/oauth2/tokenP") == 2
    assert recorder.requests[-1].url.params["KEYB"] == "20261016112900"
    assert len(result.bars) == 240
    assert result.coverage["coverage_status"] == "complete"


def test_kis_reports_unconfirmed_sessions_and_typed_errors() -> None:
    recorder = Recorder(KIS_TOKEN, kis_ok(kis_record("093000")))
    result = kis(recorder).get_bars(window("13:29", "13:31"))
    reasons = {gap["start_utc"]: gap["reason"] for gap in result.coverage["missing_intervals"]}
    # 09:29 ET is pre-market, which this KIS setup does not claim to cover.
    assert reasons == {"2026-10-16T13:29:00Z": "session_not_covered"}
    assert [bar["interval_start_utc"] for bar in result.bars] == ["2026-10-16T13:30:00Z"]

    limited = Recorder(
        KIS_TOKEN,
        j({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}),
    )
    with pytest.raises(ProviderRateLimitError) as raised:
        kis(limited).get_bars(window("13:30", "13:32"))
    assert SECRET not in str(raised.value)
    refused = Recorder(
        KIS_TOKEN, j({"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 종목입니다"})
    )
    with pytest.raises(InvalidBarsQueryError, match="OPSQ0002"):
        kis(refused).get_bars(window("13:30", "13:32"))


# --- Toss ------------------------------------------------------------------------------


def candle(end_local: str, close: Any = 773.25, currency: str = "USD") -> dict[str, Any]:
    return {
        "timestamp": f"2026-10-16T{end_local}-04:00",
        "openPrice": 773.24,
        "highPrice": 773.26,
        "lowPrice": 772.5,
        "closePrice": close,
        "volume": 4100,
        "currency": currency,
    }


def toss(recorder: Recorder) -> TossMarketDataProvider:
    config = TossConfig(client_id="toss-client", client_secret=SECRET)
    return TossMarketDataProvider(config, transport=httpx.MockTransport(recorder), now=lambda: NOW)


TOSS_TOKEN = j({"access_token": "toss-token", "token_type": "Bearer", "expires_in": 3600})


def test_toss_candle_end_timestamps_become_bar_starts_without_float_rounding() -> None:
    recorder = Recorder(
        TOSS_TOKEN,
        j(
            {
                "result": {
                    "candles": [candle("09:32:00"), candle("09:31:00", close=772.5)],
                    "nextBefore": None,
                }
            }
        ),
    )

    result = toss(recorder).get_bars(window("13:30", "13:32"))

    token, candles = recorder.requests
    assert token.url.path == "/oauth2/token"
    assert parse_qs(token.content.decode()) == {
        "grant_type": ["client_credentials"],
        "client_id": ["toss-client"],
        "client_secret": [SECRET],
    }
    assert dict(candles.url.params) == {
        "symbol": "SPY",
        "interval": "1m",
        "count": "200",
        "before": "2026-10-16T13:32:00Z",
        "adjusted": "false",
    }
    assert [bar["interval_start_utc"] for bar in result.bars] == [
        "2026-10-16T13:30:00Z",
        "2026-10-16T13:31:00Z",
    ]
    assert (result.bars[0]["close"], result.bars[0]["low"]) == ("772.5", "772.5")
    assert result.coverage["source_feed_scope"] == "toss-us-partial-venues"
    assert result.coverage["adjustment"] == "raw"


def test_toss_pages_with_next_before_and_reissues_a_revoked_token() -> None:
    recorder = Recorder(
        TOSS_TOKEN,
        j({"error": {"requestId": "r", "code": "token-revoked", "message": "revoked"}}, status=401),
        TOSS_TOKEN,
        j({"result": {"candles": [candle("09:33:00")], "nextBefore": "2026-10-16T09:32:00-04:00"}}),
        j({"result": {"candles": [candle("09:32:00"), candle("09:31:00")], "nextBefore": None}}),
    )

    result = toss(recorder).get_bars(window("13:30", "13:33"))

    assert recorder.requests[-1].url.params["before"] == "2026-10-16T09:32:00-04:00"
    assert len(result.bars) == 3


@pytest.mark.parametrize(
    ("response", "error", "fragment"),
    [
        (
            j({"error": {"requestId": "r", "code": "ip-not-allowed", "message": "no"}}, status=403),
            ProviderAuthError,
            "allowlist",
        ),
        (
            j({"error": {"requestId": "r", "code": "rate-limited", "message": "slow"}}, status=429),
            ProviderRateLimitError,
            "rate limit",
        ),
        (
            j({"result": {"candles": [candle("09:31:00", currency="KRW")], "nextBefore": None}}),
            ProviderDataError,
            "KRW",
        ),
    ],
)
def test_toss_failures_are_typed(
    response: httpx.Response, error: type[Exception], fragment: str
) -> None:
    with pytest.raises(error, match=fragment) as raised:
        toss(Recorder(TOSS_TOKEN, response)).get_bars(window("13:30", "13:32"))
    assert SECRET not in str(raised.value)


# --- Kiwoom ----------------------------------------------------------------------------


def kiwoom_row(cntr_tm: str) -> dict[str, str]:
    return {
        "cur_prc": "773.2500",
        "trde_qty": "4100",
        "open_pric": "773.2400",
        "high_pric": "773.2600",
        "low_pric": "772.5000",
        "cntr_tm": cntr_tm,
        "bus_dt": "20261016",
        "upd_stkpc_tp": "",
        "upd_rt": "",
    }


def kiwoom(recorder: Recorder, zone: Any = "America/New_York") -> KiwoomMarketDataProvider:
    config = KiwoomConfig(app_key="kiwoom-app-key", secret_key=SECRET, time_zone=zone)
    return KiwoomMarketDataProvider(
        config, transport=httpx.MockTransport(recorder), now=lambda: NOW
    )


KIWOOM_TOKEN = j(
    {
        "expires_dt": "20991231235959",
        "token_type": "bearer",
        "token": "kiwoom-token",
        "return_code": 0,
        "return_msg": "ok",
    }
)


def test_kiwoom_request_follows_the_official_spec_and_pages_with_headers() -> None:
    recorder = Recorder(
        KIWOOM_TOKEN,
        j(
            {"result_list": [kiwoom_row("20261016093100")], "return_code": 0},
            headers={"cont-yn": "Y", "next-key": "k-2"},
        ),
        j({"result_list": [kiwoom_row("20261016093000")], "return_code": 0}),
    )

    result = kiwoom(recorder).get_bars(window("13:30", "13:32"))

    token, first, second = recorder.requests
    assert (token.url.path, token.headers["api-id"]) == ("/oauth2/token", "au10001")
    assert json.loads(token.content) == {
        "grant_type": "client_credentials",
        "appkey": "kiwoom-app-key",
        "secretkey": SECRET,
    }
    assert (first.url.path, first.headers["api-id"], first.headers["authorization"]) == (
        "/api/us/chart",
        "usa06011",
        "Bearer kiwoom-token",
    )
    assert json.loads(first.content) == {
        "stex_tp": "NY",
        "stk_cd": "SPY",
        "strt_dt": "20261016",
        "tic_scope": "1",
        "upd_stkpc_tp": "0",
        "exrt_appl_tp": "0",
    }
    assert (second.headers["cont-yn"], second.headers["next-key"]) == ("Y", "k-2")
    assert [bar["interval_start_utc"] for bar in result.bars] == [
        "2026-10-16T13:30:00Z",
        "2026-10-16T13:31:00Z",
    ]


def test_kiwoom_seoul_time_zone_and_duplicate_minutes() -> None:
    seoul = Recorder(
        KIWOOM_TOKEN, j({"result_list": [kiwoom_row("20261016223000")], "return_code": 0})
    )
    assert (
        kiwoom(seoul, zone="Asia/Seoul")
        .get_bars(window("13:30", "13:31"))
        .bars[0]["interval_start_utc"]
        == "2026-10-16T13:30:00Z"
    )

    # The official example shows two rows with the same cntr_tm: never silently merged.
    duplicated = Recorder(
        KIWOOM_TOKEN,
        j(
            {
                "result_list": [kiwoom_row("20261016093000"), kiwoom_row("20261016093000")],
                "return_code": 0,
            }
        ),
    )
    with pytest.raises(ProviderDataError, match="two bars"):
        kiwoom(duplicated).get_bars(window("13:30", "13:31"))

    limited = Recorder(
        KIWOOM_TOKEN,
        j({"return_code": 1700, "return_msg": "허용된 API 요청 개수를 초과하였습니다."}),
    )
    with pytest.raises(ProviderRateLimitError):
        kiwoom(limited).get_bars(window("13:30", "13:31"))


def test_kiwoom_time_zone_defaults_to_new_york_and_can_be_overridden() -> None:
    env = {"SIW_KIWOOM_APP_KEY": "k", "SIW_KIWOOM_SECRET_KEY": SECRET}
    assert KiwoomConfig.from_env(env).time_zone == "America/New_York"
    with pytest.raises(ValueError, match="SIW_KIWOOM_TIME_ZONE"):
        KiwoomConfig.from_env({**env, "SIW_KIWOOM_TIME_ZONE": "UTC"})
    assert (
        KiwoomConfig.from_env({**env, "SIW_KIWOOM_TIME_ZONE": "Asia/Seoul"}).time_zone
        == "Asia/Seoul"
    )
    assert SECRET not in repr(KiwoomConfig.from_env({**env, "SIW_KIWOOM_TIME_ZONE": "Asia/Seoul"}))


# --- Shared safety ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/us/order"),
        ("POST", "/uapi/overseas-stock/v1/trading/order"),
        ("GET", "/api/v1/orders"),
    ],
)
def test_order_endpoints_cannot_be_called(method: str, path: str) -> None:
    recorder = Recorder()
    client = AllowlistedClient(
        "https://broker.example", [("GET", "/candles")], transport=httpx.MockTransport(recorder)
    )

    with pytest.raises(BlockedEndpointError):
        client.request(method, path)
    assert recorder.requests == []


@pytest.mark.parametrize("choice", ["kis", "toss", "kiwoom", "meritz"])
def test_broker_selection_uses_registered_keys_never_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, choice: str
) -> None:
    monkeypatch.setenv("SIW_MARKET_DATA_PROVIDER", choice)
    monkeypatch.setenv("SIW_KIWOOM_TIME_ZONE", "Asia/Seoul")
    monkeypatch.setenv("SIW_MERITZ_MAC_ADDRESS", "A1B2C3D4E5F6")
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    with pytest.raises(ProviderConfigError, match="SIW_DATA_DIR"):
        provider_from_env()

    for name in SECRET_ENV_NAMES:
        monkeypatch.setenv(name, SECRET)  # ignored: keys come only from the registry
    provider = provider_from_env(CredentialStore(tmp_path, MemorySecretBackend()))
    assert provider.instruments()
    with pytest.raises(ProviderAuthError, match="Keys screen"):
        provider.get_bars(window("13:30", "14:00"))


def test_broker_capabilities_and_instruments_follow_the_contract() -> None:
    expected = {"kis-us": "documented", "toss-us": "integration_tested"}
    for provider in (kis(Recorder()), toss(Recorder()), kiwoom(Recorder())):
        capabilities = provider.capabilities()
        validate_document(capabilities)
        assert (capabilities["source_type"], capabilities["verification_status"]) == (
            "broker_market",
            expected.get(provider.source_id, "integration_tested"),
        )
        for item in provider.instruments():
            validate_document(item["instrument"])


# --- Meritz ----------------------------------------------------------------------------

MAC = "A1B2C3D4E5F6"


def meritz_row(korea_date: str, korea_hour: str) -> dict[str, Any]:
    return {
        "korea_hour": korea_hour,
        "prpr": "773.2500",
        "cntg_vol": 4100,
        "acml_vol": 120000,
        "rltv": "96.73",
        "dcpn_cnt": 4,
        "korea_date": korea_date,
        "oprc": "773.2400",
        "hprc": "773.2600",
        "lprc": "772.5000",
    }


def meritz(recorder: Recorder) -> MeritzMarketDataProvider:
    config = MeritzConfig(app_key="meritz-app-key", app_secret=SECRET, mac_address=MAC)
    return MeritzMarketDataProvider(
        config, transport=httpx.MockTransport(recorder), now=lambda: NOW
    )


MERITZ_TOKEN = j(
    {"access_token": "meritz-token", "scope": "login", "token_type": "Bearer", "expires_in": 604800}
)


def kst(minute_of_regular_session: int) -> tuple[str, str]:
    # 09:30 ET on 2026-10-16 (EDT) is 22:30 KST the same day.
    total = 22 * 60 + 30 + minute_of_regular_session
    day = "20261016" if total < 24 * 60 else "20261017"
    total %= 24 * 60
    return day, f"{total // 60:02d}{total % 60:02d}00"


def test_meritz_request_follows_the_official_spec_and_converts_korean_time() -> None:
    recorder = Recorder(
        MERITZ_TOKEN,
        j(
            {
                "data": [meritz_row(*kst(1)), meritz_row(*kst(0))],
                "rsp_cd": "0000",
                "rsp_msg": "정상 처리 되었습니다.",
            }
        ),
    )

    result = meritz(recorder).get_bars(window("13:30", "13:32"))

    token, minutes = recorder.requests
    assert token.url.path == "/oauth2/token"
    assert parse_qs(token.content.decode()) == {
        "grant_type": ["client_credentials"],
        "client_id": ["meritz-app-key"],
        "client_secret": [SECRET],
        "scope": ["login"],
    }
    assert minutes.url.path == "/market/v1/overseas/candles/minutes"
    assert (minutes.headers["authorization"], minutes.headers["mac_address"]) == (
        "Bearer meritz-token",
        MAC,
    )
    assert dict(minutes.url.params) == {
        "mrkt_div_code": "OV",
        "mrkt_cls_code": "AX",
        "iscd": "SPY",
        "dely_rltm_cls_code": "0",
        "hour_cls_code": "60",
    }
    validate_document(result.to_document())
    assert [bar["interval_start_utc"] for bar in result.bars] == [
        "2026-10-16T13:30:00Z",
        "2026-10-16T13:31:00Z",
    ]
    assert result.coverage["adjustment"] == "unknown"


def test_meritz_capped_latest_page_marks_older_minutes_beyond_history() -> None:
    rows = [
        meritz_row(*kst(m)) for m in range(389, 349, -1)
    ]  # the latest 40 minutes of the session
    recorder = Recorder(MERITZ_TOKEN, j({"data": rows, "rsp_cd": "0000", "rsp_msg": "ok"}))

    result = meritz(recorder).get_bars(window("19:00", "20:00"))

    reasons = {
        gap["start_utc"][11:16]: gap["reason"] for gap in result.coverage["missing_intervals"]
    }
    assert len(result.bars) == 40
    assert set(reasons.values()) == {"beyond_source_history"}
    assert min(reasons) == "19:00" and max(reasons) == "19:19"


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            j({"rsp_cd": "EGW00200", "rsp_msg": "초당 거래건수를 초과하였습니다."}, status=500),
            ProviderRateLimitError,
        ),
        (
            j({"rsp_cd": "5820", "rsp_msg": "조회할 자료가 없습니다."}, status=500),
            InvalidBarsQueryError,
        ),
    ],
)
def test_meritz_errors_are_judged_by_rsp_cd(
    response: httpx.Response, error: type[Exception]
) -> None:
    with pytest.raises(error) as raised:
        meritz(Recorder(MERITZ_TOKEN, response)).get_bars(window("13:30", "13:32"))
    assert SECRET not in str(raised.value)
    assert MAC not in str(raised.value)


def test_meritz_config_requires_and_normalizes_the_mac_address() -> None:
    env = {"SIW_MERITZ_APP_KEY": "k", "SIW_MERITZ_APP_SECRET": SECRET}
    with pytest.raises(ValueError, match="SIW_MERITZ_MAC_ADDRESS"):
        MeritzConfig.from_env(env)
    with pytest.raises(ValueError, match="12 hexadecimal"):
        MeritzConfig.from_env({**env, "SIW_MERITZ_MAC_ADDRESS": "not-a-mac"})
    loaded = MeritzConfig.from_env({**env, "SIW_MERITZ_MAC_ADDRESS": "a1:b2:c3:d4:e5:f6"})
    assert loaded.mac_address == MAC
    assert SECRET not in repr(loaded) and MAC not in repr(loaded)
    validate_document(meritz(Recorder()).capabilities())

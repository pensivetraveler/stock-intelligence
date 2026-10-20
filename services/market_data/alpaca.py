"""Alpaca Market Data API v2 adapter (prototype, read-only).

Only `GET /v2/stocks/bars` is called; no trading endpoint is ever used. Credentials
come from the environment and are never logged, stored or echoed in errors.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, cast

import httpx

from packages.contracts.validator import validate_document
from services.market_data.calendar import NEW_YORK, CalendarError, UsEquityCalendar
from services.market_data.daily import (
    DailySource,
    RawDay,
    build_daily_result,
    session_date_of,
    sessions_in_range,
    validate_daily_query,
)
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    validate_bars_query,
)

Feed = Literal["sip", "iex"]
DEFAULT_DATA_URL = "https://data.alpaca.markets"
DEFAULT_SYMBOLS = "SPY:ARCX,AAPL:XNAS,MSFT:XNAS"
MAX_QUERY_MINUTES = 1_440
# Daily pages: 40 years of sessions even at a 1,000-bar page size.
DAILY_PAGES = 12
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
# Alpaca asset exchanges and their MICs. OTC and crypto assets are left out: the exchange
# calendar used here covers US equity venues only.
ASSET_EXCHANGE_MICS = {
    "NASDAQ": "XNAS",
    "NYSE": "XNYS",
    "ARCA": "ARCX",
    "AMEX": "XASE",
    "BATS": "BATS",
}
# The asset list lives on the trading API. Live and paper keys work on different hosts, so
# both are tried; only GET /v2/assets is ever called there (never orders or accounts).
TRADING_URLS = ("https://api.alpaca.markets", "https://paper-api.alpaca.markets")
ASSETS_PATH = "/v2/assets"
LISTED_INSTRUMENT = re.compile(r"^us:([a-z][a-z0-9.]{0,9}):(xnas|xnys|arcx|xase|bats):2026$")
VENUE_PATTERN = re.compile(r"^[A-Z]{4}$")
PAGE_LIMIT = 10_000


class AlpacaConfigError(ValueError):
    """The environment does not describe a usable Alpaca configuration."""


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class AlpacaConfig:
    key_id: str
    secret_key: str
    feed: Feed = "sip"
    data_url: str = DEFAULT_DATA_URL
    symbols: tuple[tuple[str, str], ...] = (("SPY", "ARCX"), ("AAPL", "XNAS"), ("MSFT", "XNAS"))
    # Minutes after a bar's end before it is published to this account. On the free plan
    # SIP history excludes the latest 15 minutes; IEX is real time.
    publication_delay_minutes: int = 15
    # Minutes after a bar's end before it is treated as final (late trades can amend bars).
    finality_minutes: int = 15
    # Rows per page; smaller values only exercise pagination (the API maximum is 10,000).
    page_limit: int = PAGE_LIMIT

    def __repr__(self) -> str:  # never print secrets, even by accident
        return (
            f"AlpacaConfig(feed={self.feed!r}, data_url={self.data_url!r}, "
            f"symbols={self.symbols!r})"
        )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AlpacaConfig:
        values = os.environ if env is None else env
        key_id = values.get("APCA_API_KEY_ID", "").strip()
        secret = values.get("APCA_API_SECRET_KEY", "").strip()
        if not key_id or not secret:
            raise AlpacaConfigError(
                "set APCA_API_KEY_ID and APCA_API_SECRET_KEY to use SIW_MARKET_DATA_PROVIDER=alpaca"
            )
        feed = values.get("SIW_ALPACA_FEED", "sip").strip().lower()
        if feed not in ("sip", "iex"):
            raise AlpacaConfigError("SIW_ALPACA_FEED must be sip or iex")
        symbols: list[tuple[str, str]] = []
        for item in values.get("SIW_ALPACA_SYMBOLS", DEFAULT_SYMBOLS).split(","):
            symbol, _, venue = item.strip().upper().partition(":")
            venue = venue or "XNAS"
            if not SYMBOL_PATTERN.fullmatch(symbol) or not VENUE_PATTERN.fullmatch(venue):
                raise AlpacaConfigError(
                    f"SIW_ALPACA_SYMBOLS entries look like SYMBOL:MIC (e.g. SPY:ARCX): {item!r}"
                )
            symbols.append((symbol, venue))
        default_delay = "15" if feed == "sip" else "0"
        try:
            delay = int(values.get("SIW_ALPACA_DELAY_MINUTES", default_delay))
            finality = int(values.get("SIW_ALPACA_FINALITY_MINUTES", "15"))
            page_limit = int(values.get("SIW_ALPACA_PAGE_LIMIT", str(PAGE_LIMIT)))
        except ValueError as exc:
            raise AlpacaConfigError("Alpaca numeric settings must be whole numbers") from exc
        if not 0 <= delay <= 1_440 or not 0 <= finality <= 1_440:
            raise AlpacaConfigError("Alpaca delay and finality must be 0-1440 minutes")
        if not 1 <= page_limit <= PAGE_LIMIT:
            raise AlpacaConfigError(f"SIW_ALPACA_PAGE_LIMIT must be 1-{PAGE_LIMIT}")
        return cls(
            key_id=key_id,
            secret_key=secret,
            feed=cast(Feed, feed),
            data_url=values.get("SIW_ALPACA_DATA_URL", DEFAULT_DATA_URL).rstrip("/"),
            symbols=tuple(symbols),
            publication_delay_minutes=delay,
            finality_minutes=finality,
            page_limit=page_limit,
        )


class AlpacaMarketDataProvider:
    """Canonical 1m bars from Alpaca, with explicit coverage for every expected minute."""

    def __init__(
        self,
        config: AlpacaConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._calendar = UsEquityCalendar("XNAS")
        self._client = httpx.Client(
            base_url=config.data_url,
            headers={
                "APCA-API-KEY-ID": config.key_id,
                "APCA-API-SECRET-KEY": config.secret_key,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport,
        )
        self._instruments = {
            symbol: f"us:{symbol.lower()}:{venue.lower()}:2026" for symbol, venue in config.symbols
        }
        self._symbols = {
            instrument_id: symbol for symbol, instrument_id in self._instruments.items()
        }
        self._venues = dict(config.symbols)

    @property
    def source_id(self) -> str:
        return f"alpaca-{self.config.feed}"

    @property
    def dataset_revision(self) -> str:
        # Alpaca exposes no data revisions. Final bars are cached and not refetched, and
        # disagreeing overlaps fail loudly in the cache, so one revision per feed holds.
        return f"alpaca-{self.config.feed}-raw-r1"

    def capabilities(self) -> dict[str, Any]:
        return {
            "kind": "source_capabilities",
            "schema_version": "0.1",
            "source_id": self.source_id,
            "source_type": "paid_market",
            # A live account check succeeded on 2026-10-17 (SPY, sip, 30/30 minutes; ADR 0012).
            # Plan entitlement still varies by feed and range, so it stays unknown.
            "verification_status": "integration_tested",
            "entitlement_status": "unknown",
            "license_status": "unknown",
            "feeds": [self.config.feed],
            "rights_local_retention": "unknown",
            "observed_at_utc": _utc_text(self._now()),
        }

    def symbol_for(self, instrument_id: str) -> str | None:
        """Alpaca symbol of a configured instrument or of any listed US equity ID."""
        configured = self._symbols.get(instrument_id)
        if configured is not None:
            return configured
        match = LISTED_INSTRUMENT.fullmatch(instrument_id)
        return match.group(1).upper() if match else None

    def list_assets(self) -> list[dict[str, str]]:
        """Active US-listed stocks and ETFs: symbol, company name and venue MIC."""
        response: httpx.Response | None = None
        for base in TRADING_URLS:
            try:
                response = self._client.get(
                    f"{base}{ASSETS_PATH}",
                    params={"status": "active", "asset_class": "us_equity"},
                    timeout=httpx.Timeout(30.0, connect=5.0),
                )
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    f"could not reach Alpaca: {type(exc).__name__}"
                ) from exc
            if response.status_code not in (401, 403):
                break  # a live key is refused by the paper host and the other way round
        assert response is not None
        self._raise_for_status(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderDataError("Alpaca returned an asset list that is not JSON") from exc
        if not isinstance(payload, list):
            raise ProviderDataError("Alpaca returned an asset list that is not a list")
        assets: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).upper()
            venue = ASSET_EXCHANGE_MICS.get(str(item.get("exchange", "")))
            if venue is None or not SYMBOL_PATTERN.fullmatch(symbol):
                continue
            name = " ".join(str(item.get("name") or "").split())[:200]
            assets.append({"symbol": symbol, "name": name, "venue": venue})
        return assets

    def instruments(self) -> list[dict[str, Any]]:
        return [
            {
                "instrument": {
                    "kind": "instrument_ref",
                    "schema_version": "0.1",
                    "instrument_id": instrument_id,
                    "symbol": symbol,
                    "venue": self._venues[symbol],
                    "asset_class": "us_equity",
                    "currency": "USD",
                    "identity_as_of": "2026-01-01T00:00:00Z",
                    "identity_source": "alpaca-config",
                },
                "source_id": self.source_id,
                "dataset_revision": self.dataset_revision,
                "session_dates": [],
            }
            for symbol, instrument_id in self._instruments.items()
        ]

    def get_bars(self, query: BarsQuery) -> BarsResult:
        self._validate(query)
        symbol = self.symbol_for(query.instrument_id)
        if symbol is None:
            raise InstrumentNotFoundError("instrument is not a listed US equity ID")
        now = self._now()
        try:
            expected = [
                start for start, _ in self._calendar.trading_minutes(query.start_utc, query.end_utc)
            ]
        except CalendarError as exc:
            raise InvalidBarsQueryError(str(exc)) from exc
        published_until = now - timedelta(minutes=self.config.publication_delay_minutes)
        request_end = min(query.end_utc, published_until.replace(second=0, microsecond=0))
        raw = (
            self._fetch(symbol, query.start_utc, request_end)
            if request_end > query.start_utc
            else []
        )

        final_before = now - timedelta(minutes=self.config.finality_minutes)
        bars_by_start: dict[datetime, dict[str, Any]] = {}
        for item in raw:
            bar = self._canonical(query.instrument_id, item, final_before)
            if bar is None:
                continue
            start = _parse_utc(bar["interval_start_utc"])
            if start in bars_by_start:
                raise ProviderDataError("the provider returned two bars for the same minute")
            bars_by_start[start] = bar

        visible: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []
        for start in expected:
            end = start + timedelta(minutes=1)
            bar = bars_by_start.get(start)
            if bar is not None and (
                query.as_of_utc is None or _parse_utc(bar["available_at_utc"]) <= query.as_of_utc
            ):
                visible.append(bar)
                continue
            if bar is not None:
                reason = "not_available_as_of_cutoff"
            elif end > request_end:
                reason = "outside_entitlement"
            elif end > final_before:
                reason = "not_yet_final"
            elif query.as_of_utc is not None and (
                end + timedelta(minutes=self.config.publication_delay_minutes) > query.as_of_utc
            ):
                reason = "not_available_as_of_cutoff"
            else:
                reason = "provider_no_bar"
            missing.append(
                {"start_utc": _utc_text(start), "end_utc": _utc_text(end), "reason": reason}
            )

        coverage = {
            "kind": "coverage_manifest",
            "schema_version": "0.1",
            "instrument_id": query.instrument_id,
            "timeframe": "1m",
            "requested_start_utc": _utc_text(query.start_utc),
            "requested_end_utc": _utc_text(query.end_utc),
            "coverage_status": (
                "complete" if visible and not missing else ("partial" if visible else "empty")
            ),
            "verification_status": "unverified",
            "observed_interval_count": len(visible),
            "expected_interval_count": len(expected),
            "missing_intervals": missing,
            "source_id": self.source_id,
            "source_feed_scope": f"alpaca-{self.config.feed}",
            "adjustment": "raw",
            "dataset_revision": self.dataset_revision,
            "point_in_time_cutoff_utc": (
                _utc_text(query.as_of_utc) if query.as_of_utc is not None else None
            ),
        }
        result = BarsResult(tuple(visible), coverage)
        try:
            validate_document(result.to_document())
        except ValueError as exc:
            raise ProviderDataError(f"provider data violates the contract: {exc}") from exc
        return result

    def get_daily_bars(self, query: BarsQuery) -> BarsResult:
        """Daily bars (Alpaca "1Day", unadjusted) for sessions opening in the range."""
        now = self._now()
        validate_daily_query(query, "1d", now)
        symbol = self.symbol_for(query.instrument_id)
        if symbol is None:
            raise InstrumentNotFoundError("instrument is not a listed US equity ID")
        try:
            sessions = sessions_in_range(self._calendar, query.start_utc, query.end_utc)
        except CalendarError as exc:
            raise InvalidBarsQueryError(str(exc)) from exc
        raw: dict[date, RawDay] = {}
        published_until = now - timedelta(minutes=self.config.publication_delay_minutes)
        if sessions and sessions[0].regular_open_utc < published_until:
            start = datetime.combine(sessions[0].session_date, time(0, 0), NEW_YORK)
            end = min(sessions[-1].regular_close_utc, published_until.replace(microsecond=0))
            for item in self._fetch_pages(symbol, "1Day", start, end, DAILY_PAGES):
                try:
                    day = session_date_of(str(item["t"]))
                    values = {key: Decimal(str(item[key])) for key in ("o", "h", "l", "c", "v")}
                except (KeyError, ValueError, ArithmeticError) as exc:
                    raise ProviderDataError("Alpaca returned a malformed daily bar") from exc
                if day in raw:
                    raise ProviderDataError("Alpaca returned two daily bars for one session")
                raw[day] = RawDay(
                    day, values["o"], values["h"], values["l"], values["c"], values["v"]
                )
        source = DailySource(
            source_id=self.source_id,
            feed_scope=f"alpaca-{self.config.feed}",
            dataset_revision=f"{self.dataset_revision}:1d",
            adjustment="raw",
            publication_delay_minutes=self.config.publication_delay_minutes,
            finality_minutes=self.config.finality_minutes,
        )
        return build_daily_result(query, raw, source, self._calendar, now)

    def _validate(self, query: BarsQuery) -> None:
        if query.timeframe != "1m":
            raise InvalidBarsQueryError("the Alpaca adapter serves canonical 1m bars")
        validate_bars_query(query)
        for name, value in (("start_utc", query.start_utc), ("end_utc", query.end_utc)):
            if value.second:
                raise InvalidBarsQueryError(f"{name} must align to a minute boundary")
        if query.end_utc - query.start_utc > timedelta(minutes=MAX_QUERY_MINUTES):
            raise InvalidBarsQueryError(f"range cannot exceed {MAX_QUERY_MINUTES} minutes")
        if query.as_of_utc is not None and query.as_of_utc > self._now():
            raise InvalidBarsQueryError("as_of_utc cannot be in the future")

    def _fetch(self, symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        # Enough pages for a full 1,440-minute range even at small test page sizes.
        return self._fetch_pages(
            symbol, "1Min", start, end, MAX_QUERY_MINUTES // self.config.page_limit + 2
        )

    def _fetch_pages(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, max_pages: int
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": _utc_text(start),
            "end": _utc_text(end),
            "limit": self.config.page_limit,
            "adjustment": "raw",
            "feed": self.config.feed,
            "sort": "asc",
        }
        bars: list[dict[str, Any]] = []
        for _page in range(max_pages):
            try:
                response = self._client.get("/v2/stocks/bars", params=params)
            except httpx.TimeoutException as exc:
                raise ProviderUnavailableError("Alpaca did not answer in time") from exc
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    f"could not reach Alpaca: {type(exc).__name__}"
                ) from exc
            self._raise_for_status(response)
            try:
                payload = json.loads(response.text, parse_float=Decimal)
            except ValueError as exc:
                raise ProviderDataError("Alpaca returned a response that is not JSON") from exc
            bars.extend(payload.get("bars", {}).get(symbol, []) or [])
            token = payload.get("next_page_token")
            if not token:
                return bars
            params["page_token"] = token
        raise ProviderDataError("Alpaca pagination did not finish")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            detail = str(json.loads(response.text).get("message", ""))[:200]
        except (ValueError, AttributeError):
            detail = ""
        suffix = f": {detail}" if detail else ""
        if response.status_code in (401, 403):
            raise ProviderAuthError(
                f"Alpaca rejected the request ({response.status_code}){suffix}. "
                "Check the API keys and that the plan covers this feed and range."
            )
        if response.status_code == 429:
            raise ProviderRateLimitError("Alpaca rate limit reached; retry later")
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"Alpaca is unavailable ({response.status_code})")
        raise InvalidBarsQueryError(f"Alpaca refused the query ({response.status_code}){suffix}")

    def _canonical(
        self, instrument_id: str, item: dict[str, Any], final_before: datetime
    ) -> dict[str, Any] | None:
        try:
            start = _parse_utc(str(item["t"]))
            values = {key: Decimal(str(item[key])) for key in ("o", "h", "l", "c", "v")}
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise ProviderDataError("Alpaca returned a malformed bar") from exc
        end = start + timedelta(minutes=1)
        session = self._calendar.classify_interval(start, end)
        if session not in ("pre", "regular", "post"):
            return None  # outside the versioned extended-hours policy (04:00-20:00 ET)
        final = end <= final_before
        return {
            "kind": "price_bar",
            "schema_version": "0.1",
            "instrument_id": instrument_id,
            "timeframe": "1m",
            "interval_start_utc": _utc_text(start),
            "interval_end_utc": _utc_text(end),
            "open": format(values["o"], "f"),
            "high": format(values["h"], "f"),
            "low": format(values["l"], "f"),
            "close": format(values["c"], "f"),
            "volume": format(values["v"], "f"),
            "volume_unit": "shares",
            "session": session,
            "is_final": final,
            "quality": "complete" if final else "provisional",
            "lineage": {
                "kind": "provider",
                "source_timeframe": "1m",
                "expected_source_bar_count": 1,
                "observed_source_bar_count": 1,
                "source_dataset_revision": None,
                "calendar_id": "XNAS",
                "calendar_version": self._calendar.calendar_version,
            },
            "adjustment": "raw",
            "source_id": self.source_id,
            "source_feed_scope": f"alpaca-{self.config.feed}",
            "dataset_revision": self.dataset_revision,
            # Estimated: the bar's end plus this account's publication delay.
            "available_at_utc": _utc_text(
                end + timedelta(minutes=self.config.publication_delay_minutes)
            ),
        }


def _check(argv: list[str]) -> int:
    """Live smoke test with the user's own keys: one small request, no secrets printed."""
    parser = argparse.ArgumentParser(prog="python -m services.market_data.alpaca check")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    parser.add_argument("--label", default=None, help="use only the registered key with this label")
    args = parser.parse_args(argv)
    # Keys come from the Keys screen (labels in SIW_DATA_DIR, secrets in the OS keychain).
    from services.market_data.selection import (
        ProviderConfigError,
        default_credential_store,
        pooled_provider,
    )

    try:
        provider = pooled_provider(
            "alpaca",
            default_credential_store(),
            only=(lambda record: record.label == args.label) if args.label else None,
        )
    except ProviderConfigError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    listings = {
        item["instrument"]["symbol"]: item["instrument"]["instrument_id"]
        for item in provider.instruments()
    }
    symbol = (args.symbol or next(iter(listings))).upper()
    instrument_id = listings.get(symbol)
    if instrument_id is None:
        print(json.dumps({"status": "error", "error": f"{symbol} is not in SIW_ALPACA_SYMBOLS"}))
        return 2
    day = args.date or (datetime.now(timezone.utc) - timedelta(days=1)).date()
    calendar = UsEquityCalendar("XNAS")
    window = calendar.window_for_date(day)
    while window is None:
        day -= timedelta(days=1)
        window = calendar.window_for_date(day)
    start = window.regular_open_utc
    query = BarsQuery(instrument_id, "1m", start, start + timedelta(minutes=30))
    try:
        result = provider.get_bars(query)
    except (
        ProviderAuthError,
        ProviderRateLimitError,
        ProviderUnavailableError,
        ProviderDataError,
        InvalidBarsQueryError,
    ) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "error": str(exc)}))
        return 1
    coverage = result.coverage
    print(
        json.dumps(
            {
                "status": "ok",
                "source_id": coverage["source_id"],
                "symbol": symbol,
                "session_date": day.isoformat(),
                "range_utc": [coverage["requested_start_utc"], coverage["requested_end_utc"]],
                "bars": len(result.bars),
                "expected_minutes": coverage["expected_interval_count"],
                "missing_reasons": sorted({gap["reason"] for gap in coverage["missing_intervals"]}),
                "first_bar": result.bars[0] if result.bars else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] != ["check"]:
        print(
            "usage: python -m services.market_data.alpaca check [--symbol SPY] [--date YYYY-MM-DD]"
        )
        raise SystemExit(2)
    raise SystemExit(_check(sys.argv[2:]))

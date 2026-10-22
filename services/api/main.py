from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import uuid4

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from packages.contracts.validator import validate_document
from services.agents.commentary import ask_claude
from services.agents.store import AgentStore
from services.api.agent_routes import register_agent_routes
from services.api.backtest_routes import register_backtest_routes
from services.api.credential_routes import register_credential_routes
from services.api.indicator_routes import register_indicator_routes
from services.api.journal_routes import register_import_routes, register_journal_routes
from services.backtest.jobs import BacktestRunner
from services.backtest.store import BacktestStore
from services.credentials.catalog import adapter_settings
from services.credentials.pool import PooledProvider
from services.credentials.store import CredentialStore, KeyringSecretBackend, SecretStorageError
from services.custom_indicators.store import CustomIndicatorStore
from services.journal.ledger import account_positions
from services.journal.store import JournalStore
from services.market_data.aggregation import AggregationError
from services.market_data.bars_service import MarketBarsService
from services.market_data.calendar import CalendarError, UsEquityCalendar
from services.market_data.instruments import market_of, parse_instrument
from services.market_data.kiwoom_account import NEW_YORK as KIWOOM_NEW_YORK
from services.market_data.kiwoom_account import (
    KiwoomAccountCredentials,
    KiwoomAccountSession,
)
from services.market_data.kiwoom_account import sync_executions as kiwoom_sync
from services.market_data.market_router import MarketRouter
from services.market_data.provider import (
    BarsQuery,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    MarketDataProvider,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from services.market_data.selection import build_adapter, chosen_provider, provider_from_env
from services.market_data.storage import (
    CachedMarketDataProvider,
    LocalMarketDataStore,
    StorageError,
)
from services.market_data.symbol_directory import (
    MAX_QUERY_LENGTH,
    MAX_RESULTS,
    SymbolDirectory,
    SymbolDirectoryUnavailableError,
    SymbolEntry,
    search_all,
)
from services.market_data.tick_store import TickStore
from services.market_data.toss import TossConfig, TossMarketDataProvider
from services.market_data.toss_account import (
    account_snapshot,
    sellable_quantity,
    sync_executions,
)
from services.market_data.toss_info import (
    exchange_rate,
    korean_listings,
    korean_names,
    market_calendar,
    stock_info,
)
from services.market_data.toss_quotes import market_quote
from services.market_data.toss_research import research_table
from services.market_data.toss_session import (
    DEFAULT_BASE_URL as DEFAULT_TOSS_URL,
)
from services.market_data.toss_session import (
    TossCredentials,
    TossSession,
    session_for,
)
from services.market_data.toss_stream import Connect, RealtimeHub, default_connect
from services.public_sources.finra import FinraCredential
from services.public_sources.sources import PublicSources

LOG = logging.getLogger("siw.api")
APP_NAME = "stock-intelligence-workstation-api"
# US tickers start with a letter; Korean KRX codes are six characters starting with a digit.
REALTIME_SYMBOL = re.compile(r"^([A-Z][A-Z0-9.]{0,9}|[0-9][0-9A-Z]{5})$")
MAX_REALTIME_SYMBOLS = 45
MAX_TICKS_RETURNED = 5_000
APP_VERSION = "0.4.0-dev"
API_VERSION = "v1"
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "tauri://localhost",
)
# The Electron shell serves the screen from this origin (ADR 0028). It is allowed even
# when SIW_CORS_ORIGINS replaces the defaults: no web page can send a siw:// origin.
APP_SHELL_ORIGINS = ("siw://app",)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
MAX_SESSION_LIST = 120


def _cors_origins() -> list[str]:
    configured = os.getenv("SIW_CORS_ORIGINS")
    origins = (
        list(DEFAULT_CORS_ORIGINS)
        if configured is None
        else [origin.strip() for origin in configured.split(",") if origin.strip()]
    )
    return origins + [origin for origin in APP_SHELL_ORIGINS if origin not in origins]


def _incoming_request_id(request: Request) -> str:
    # Echo a caller's ID only when it is short and log-safe; otherwise mint one.
    value = request.headers.get("X-Request-ID")
    if value is not None and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid4())


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else str(uuid4())


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
        # Server errors bypass the request-ID middleware, so set the header here too.
        headers={"X-Request-ID": request_id},
    )


def _default_market_data_provider(credentials: CredentialStore | None) -> MarketDataProvider:
    upstream = provider_from_env(credentials)
    configured_data_dir = os.getenv("SIW_DATA_DIR", "").strip()
    if not configured_data_dir:
        return upstream
    return CachedMarketDataProvider(upstream, LocalMarketDataStore(Path(configured_data_dir)))


class _LazyCredentials:
    """Open the key registry on first use; None when no data directory is configured."""

    def __init__(self, store: CredentialStore | None) -> None:
        self._store = store
        self._lock = threading.Lock()

    def get(self) -> CredentialStore | None:
        with self._lock:
            if self._store is None:
                configured = os.getenv("SIW_DATA_DIR", "").strip()
                if configured:
                    self._store = CredentialStore(Path(configured), KeyringSecretBackend())
            return self._store


class _LazyProvider:
    """Build the default provider on first use, so importing the module has no side effects."""

    def __init__(
        self,
        provider: MarketDataProvider | None,
        credentials: Callable[[], CredentialStore | None],
        korean: Callable[[], MarketDataProvider | None],
    ) -> None:
        self._provider = provider
        self._credentials = credentials
        self._korean = korean
        self._router: MarketRouter | None = None
        self._lock = threading.Lock()

    def get(self) -> MarketRouter:
        with self._lock:
            if self._router is None:
                us = self._provider or _default_market_data_provider(self._credentials())
                self._router = MarketRouter(us, self._korean)
            return self._router

    def key_status(self) -> dict[str, dict[str, object]]:
        """Per-key runtime state when the provider is served through registered keys."""
        upstream = getattr(self.get(), "upstream", self.get())
        return upstream.status() if isinstance(upstream, PooledProvider) else {}


class _LazySymbols:
    """The symbol directory of the running provider, when it offers one and data is kept."""

    def __init__(
        self,
        directory: SymbolDirectory | None,
        provider: _LazyProvider,
        toss: Callable[[], TossSession | None],
        korean: SymbolDirectory | None = None,
    ) -> None:
        self._directory = directory
        self._provider = provider
        self._toss = toss
        self._injected_korean = korean
        self._korean_directory: SymbolDirectory | None = None
        self._lock = threading.Lock()

    def _korean_names(self) -> dict[str, str]:
        session = self._toss()
        return korean_names(session) if session is not None else {}

    def get(self) -> SymbolDirectory | None:
        with self._lock:
            if self._directory is None:
                configured = os.getenv("SIW_DATA_DIR", "").strip()
                upstream = self._provider.get()
                list_assets = getattr(upstream, "list_assets", None)
                if configured and list_assets is not None:
                    self._directory = SymbolDirectory(
                        Path(configured),
                        upstream.source_id,
                        list_assets,
                        local_names=self._korean_names,
                    )
            return self._directory

    def korean(self) -> SymbolDirectory | None:
        """Korean listings (KOSPI, KOSDAQ) from Toss, when a Toss key is registered."""
        with self._lock:
            if self._korean_directory is None and self._injected_korean is None:
                configured = os.getenv("SIW_DATA_DIR", "").strip()
                if configured and self._toss() is not None:
                    self._korean_directory = SymbolDirectory(
                        Path(configured), "toss-kr", self._korean_listings
                    )
            return self._injected_korean or self._korean_directory

    def _korean_listings(self) -> list[dict[str, str]]:
        session = self._toss()
        if session is None:
            raise SymbolDirectoryUnavailableError("no enabled real-account Toss key")
        return korean_listings(session)

    def searchable(self) -> list[SymbolDirectory]:
        return [item for item in (self.get(), self.korean()) if item is not None]

    def known(self) -> dict[str, SymbolEntry]:
        """US and Korean listings known on disk (never triggers a provider request)."""
        merged: dict[str, SymbolEntry] = {}
        for directory in (self.get(), self.korean()):
            if directory is not None:
                merged.update(directory.known())
        return merged


def _toss_credentials(credentials: CredentialStore | None) -> TossCredentials | None:
    """The first enabled real-account Toss key on the Keys screen."""
    if credentials is None:
        return None
    for record in credentials.list("toss"):
        if record.enabled and record.environment == "real":
            secrets = credentials.secrets(record)
            base_url = os.getenv("SIW_TOSS_BASE_URL", DEFAULT_TOSS_URL).strip().rstrip("/")
            return TossCredentials(secrets["client_id"], secrets["client_secret"], base_url)
    return None


class _LazyBoats:
    """Day-market bars (ADR 0030): an Alpaca adapter on the first enabled Alpaca key,
    whichever provider serves the charts. Rebuilt when that key changes."""

    def __init__(self, credentials: Callable[[], CredentialStore | None]) -> None:
        self._credentials = credentials
        self._key: tuple[str, str] | None = None
        self._adapter: Any = None
        self._lock = threading.Lock()

    def get(self) -> Any:
        store = self._credentials()
        if store is None:
            return None
        record = next((item for item in store.list("alpaca") if item.enabled), None)
        if record is None:
            return None
        secrets = store.secrets(record)
        key = (secrets["api_key_id"], secrets["api_secret_key"])
        with self._lock:
            if self._adapter is None or self._key != key:
                settings = adapter_settings(os.environ, "alpaca", secrets, record.environment)
                self._adapter = build_adapter("alpaca", settings)
                self._key = key
            return self._adapter


def _kiwoom_session(credentials: CredentialStore | None) -> KiwoomAccountSession | None:
    """A read-only account session of the first enabled real-account Kiwoom key."""
    if credentials is None:
        return None
    for record in credentials.list("kiwoom"):
        if record.enabled and record.environment == "real":
            secrets = credentials.secrets(record)
            return KiwoomAccountSession(
                KiwoomAccountCredentials(secrets["app_key"], secrets["secret_key"], "real")
            )
    return None


def _toss_session(credentials: CredentialStore | None) -> TossSession | None:
    """The shared session of that key (one token per key for the whole process)."""
    found = _toss_credentials(credentials)
    return session_for(found) if found is not None else None


class _LazyKoreanProvider:
    """Toss bars for Korean listings, cached like any provider when SIW_DATA_DIR is set."""

    def __init__(
        self,
        provider: MarketDataProvider | None,
        credentials: Callable[[], CredentialStore | None],
    ) -> None:
        self._provider = provider
        self._credentials = credentials
        self._key: tuple[str, str] | None = None
        self._lock = threading.Lock()

    def get(self) -> MarketDataProvider | None:
        with self._lock:
            if self._provider is not None and self._key is None:
                return self._provider  # injected
            found = _toss_credentials(self._credentials())
            if found is None:
                return None
            key = (found.client_id, found.client_secret)
            if self._provider is None or self._key != key:
                adapter = TossMarketDataProvider(
                    TossConfig(
                        client_id=found.client_id,
                        client_secret=found.client_secret,
                        base_url=found.base_url,
                        market="KR",
                    )
                )
                configured = os.getenv("SIW_DATA_DIR", "").strip()
                self._provider = (
                    CachedMarketDataProvider(adapter, LocalMarketDataStore(Path(configured)))
                    if configured
                    else adapter
                )
                self._key = key
            return self._provider


class _LazyJournal:
    """Open the journal store on first use; None when no data directory is configured."""

    def __init__(self, store: JournalStore | None) -> None:
        self._store = store
        self._lock = threading.Lock()

    def get(self) -> JournalStore | None:
        with self._lock:
            if self._store is None:
                configured = os.getenv("SIW_DATA_DIR", "").strip()
                if configured:
                    self._store = JournalStore(Path(configured))
            return self._store


class _LazyBacktests:
    """Open the backtest store and start its runner on first use (needs SIW_DATA_DIR)."""

    def __init__(
        self,
        runner: BacktestRunner | None,
        provider: Callable[[], MarketDataProvider],
        clock: Callable[[], datetime],
        indicators: Callable[[], CustomIndicatorStore | None] = lambda: None,
    ) -> None:
        self._runner = runner
        self._provider = provider
        self._indicators = indicators
        self._clock = clock
        self._lock = threading.Lock()

    def get(self) -> BacktestRunner | None:
        with self._lock:
            if self._runner is None:
                configured = os.getenv("SIW_DATA_DIR", "").strip()
                if configured:
                    store = BacktestStore(Path(configured) / "backtests.sqlite3", self._clock)
                    self._runner = BacktestRunner(
                        store,
                        lambda: MarketBarsService(self._provider()).get_bars,
                        get_adjusted=lambda: getattr(
                            self._provider(), "get_adjusted_daily_bars", None
                        ),
                        indicators=self._indicators,
                    )
            return self._runner

    def started(self) -> BacktestRunner | None:
        return self._runner


class _LazyAgents:
    """Open the Agent analysis store on first use (needs SIW_DATA_DIR)."""

    def __init__(self, store: AgentStore | None, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()

    def get(self) -> AgentStore | None:
        with self._lock:
            if self._store is None:
                configured = os.getenv("SIW_DATA_DIR", "").strip()
                if configured:
                    self._store = AgentStore(Path(configured) / "agents.sqlite3", self._clock)
            return self._store


class _LazyIndicators:
    """Open the custom indicator store on first use (needs SIW_DATA_DIR)."""

    def __init__(self, store: CustomIndicatorStore | None, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()

    def get(self) -> CustomIndicatorStore | None:
        with self._lock:
            if self._store is None:
                configured = os.getenv("SIW_DATA_DIR", "").strip()
                if configured:
                    self._store = CustomIndicatorStore(
                        Path(configured) / "indicators.sqlite3", self._clock
                    )
            return self._store


class _LazyPublicSources:
    """Open the SEC/FINRA cache on first use (needs SIW_DATA_DIR; ADR 0024)."""

    def __init__(
        self,
        sources: PublicSources | None,
        clock: Callable[[], datetime],
        credentials: Callable[[], CredentialStore | None],
    ) -> None:
        self._sources = sources
        self._clock = clock
        self._credentials = credentials
        self._lock = threading.Lock()

    def get(self) -> PublicSources | None:
        with self._lock:
            if self._sources is None:
                configured = os.getenv("SIW_DATA_DIR", "").strip()
                if configured:
                    self._sources = PublicSources(
                        Path(configured) / "public_sources.sqlite3",
                        self._clock,
                        user_agent=lambda: os.getenv("SIW_SEC_USER_AGENT"),
                        finra_credential=lambda: _finra_credential(self._credentials()),
                    )
            return self._sources


def _finra_credential(credentials: CredentialStore | None) -> FinraCredential | None:
    """The first enabled FINRA API credential on the Keys screen."""
    if credentials is None:
        return None
    for record in credentials.list("finra"):
        if record.enabled:
            secrets = credentials.secrets(record)
            return FinraCredential(secrets["client_id"], secrets["client_secret"])
    return None


def _claude_key(credentials: CredentialStore | None) -> str | None:
    """The first enabled Claude (Anthropic) key on the Keys screen."""
    if credentials is None:
        return None
    for record in credentials.list("anthropic"):
        if record.enabled:
            return credentials.secrets(record)["api_key"]
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_app(
    market_data_provider: MarketDataProvider | None = None,
    *,
    journal_store: JournalStore | None = None,
    credential_store: CredentialStore | None = None,
    symbol_directory: SymbolDirectory | None = None,
    toss_session: Callable[[], TossSession | None] | None = None,
    kiwoom_session: Callable[[], KiwoomAccountSession | None] | None = None,
    overnight_source: Callable[[], Any] | None = None,
    tick_store: TickStore | None = None,
    korean_provider: MarketDataProvider | None = None,
    korean_directory: SymbolDirectory | None = None,
    realtime_connect: Connect | None = None,
    backtest_runner: BacktestRunner | None = None,
    agent_store: AgentStore | None = None,
    agent_ask: Callable[..., dict[str, Any]] = ask_claude,
    indicator_store: CustomIndicatorStore | None = None,
    public_sources: PublicSources | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> FastAPI:
    credentials = _LazyCredentials(credential_store)
    korean = _LazyKoreanProvider(korean_provider, credentials.get)
    provider = _LazyProvider(market_data_provider, credentials.get, korean.get)
    toss = toss_session or (lambda: _toss_session(credentials.get()))
    kiwoom = kiwoom_session or (lambda: _kiwoom_session(credentials.get()))
    boats = overnight_source or _LazyBoats(credentials.get).get
    symbols = _LazySymbols(symbol_directory, provider, toss, korean_directory)
    ticks_holder: list[TickStore | None] = [tick_store]

    def ticks() -> TickStore | None:
        if ticks_holder[0] is None:
            configured = os.getenv("SIW_DATA_DIR", "").strip()
            if configured:
                ticks_holder[0] = TickStore(Path(configured))
        return ticks_holder[0]

    def instrument_for(symbol: str, currency: str = "USD") -> str | None:
        """Instrument ID of a US ticker (USD) or Korean code (KRW).

        US: configured instruments first, then the directory. Korean: the Korean directory.
        """
        korean = currency == "KRW"
        if not korean:
            for item in provider.get().instruments():
                if item["instrument"]["symbol"].upper() == symbol:
                    return cast(str, item["instrument"]["instrument_id"])
        for instrument_id, entry in symbols.known().items():
            if entry.symbol == symbol and entry.korean == korean:
                return instrument_id
        return None

    def sync_recent(since: str) -> dict[str, object] | None:
        session, store = toss(), journal.get()
        if session is None or store is None:
            return None
        return sync_executions(session, store, instrument_for, clock, since)

    hub = RealtimeHub(
        toss,
        ticks=ticks,
        instrument_for=instrument_for,
        sync_ledger=sync_recent,
        connect=realtime_connect or default_connect,
        now=clock,
    )
    journal = _LazyJournal(journal_store)
    custom_indicators = _LazyIndicators(indicator_store, clock)
    backtests = _LazyBacktests(backtest_runner, provider.get, clock, custom_indicators.get)
    agents = _LazyAgents(agent_store, clock)
    sources = _LazyPublicSources(public_sources, clock, credentials.get)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        # Under a server, open the fixture and cache at startup so misconfiguration fails
        # fast; clients that skip lifespan events still get the provider on first request.
        provider.get()
        yield
        store = ticks_holder[0]
        if store is not None:
            store.flush()  # ticks still in memory when the server stops
        runner = backtests.started()
        if runner is not None:
            runner.stop()  # a run in progress is cancelled, not left half-written

    application = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

    async def unexpected_errors_inside_cors(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Exception handlers for `Exception` run outside every middleware, so their
        # response lacks CORS headers and a browser reports only a network failure.
        # Converting the error here, inside CORSMiddleware, keeps the envelope readable.
        try:
            return await call_next(request)
        except Exception:
            return _error_response(request, 500, "internal_error", "Internal server error")

    application.add_middleware(BaseHTTPMiddleware, dispatch=unexpected_errors_inside_cors)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Accept", "Content-Type", "X-Request-ID"],
    )

    @application.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _incoming_request_id(request)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = "Resource not found" if exc.status_code == 404 else "Request failed"
        return _error_response(request, exc.status_code, code, message)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error_response(request, 422, "validation_error", "Request validation failed")

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
        return _error_response(request, 500, "internal_error", "Internal server error")

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/version")
    async def version() -> dict[str, str]:
        return {
            "name": APP_NAME,
            "api_version": API_VERSION,
            "app_version": APP_VERSION,
        }

    @application.get("/v1/market/instruments", response_model=None)
    def market_instruments() -> Response:
        document = {
            "kind": "market_instruments_response",
            "schema_version": "0.1",
            "instruments": provider.get().instruments(),
        }
        validate_document(document)
        return JSONResponse(content=document)

    @application.get("/v1/market/symbols", response_model=None)
    def market_symbols(
        request: Request,
        q: Annotated[str, Query(max_length=MAX_QUERY_LENGTH)] = "",
        limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 20,
    ) -> Response:
        """US and Korean listings whose ticker, code or company name matches `q`."""
        directories = symbols.searchable()
        if not directories:
            return _error_response(
                request,
                404,
                "symbol_search_unavailable",
                "the current data provider offers no symbol list, or SIW_DATA_DIR is not set",
            )
        try:
            found, fetched_at, stale = search_all(directories, q, limit)
        except SymbolDirectoryUnavailableError as exc:
            return _error_response(request, 503, "symbol_directory_unavailable", str(exc))
        document = {
            "kind": "symbol_search_response",
            "schema_version": "0.1",
            "query": q,
            "source_id": directories[0].source_id,
            "fetched_at_utc": fetched_at,
            "stale": stale,
            "results": [
                {
                    "listing": directory.listing(entry),
                    "name": entry.name,
                    "local_name": entry.local_name,
                }
                for directory, entry in found
            ],
        }
        validate_document(document)
        return JSONResponse(content=document)

    def toss_read(request: Request, read: Callable[[TossSession], dict[str, object]]) -> Response:
        """Run one on-demand Toss read and map its failures to the API's error envelope."""
        return broker_read(request, toss, "Toss", read)

    def broker_read(
        request: Request,
        open_session: Callable[[], Any],
        broker: str,
        read: Callable[[Any], dict[str, object]],
    ) -> Response:
        try:
            session = open_session()
        except SecretStorageError as exc:
            return _error_response(request, 502, "provider_auth_failed", str(exc))
        if session is None:
            return _error_response(
                request,
                404,
                "quote_source_unavailable",
                f"register an enabled real-account {broker} key on the Keys screen for live data",
            )
        try:
            document = read(session)
        except InstrumentNotFoundError as exc:
            return _error_response(request, 404, "instrument_not_found", str(exc))
        except InvalidBarsQueryError as exc:
            return _error_response(request, 422, "invalid_market_data_query", str(exc))
        except ProviderAuthError as exc:
            return _error_response(request, 502, "provider_auth_failed", str(exc))
        except ProviderRateLimitError as exc:
            return _error_response(request, 503, "provider_rate_limited", str(exc))
        except ProviderUnavailableError as exc:
            return _error_response(request, 503, "provider_unavailable", str(exc))
        except ProviderDataError as exc:
            return _error_response(request, 502, "provider_data_invalid", str(exc))
        return JSONResponse(content=document, headers={"Cache-Control": "no-store"})

    def symbol_of(instrument_id: str) -> str | None:
        parsed = parse_instrument(instrument_id)
        if parsed is not None:
            return parsed.symbol
        return next(
            (
                item["instrument"]["symbol"]
                for item in provider.get().instruments()
                if item["instrument"]["instrument_id"] == instrument_id
            ),
            None,
        )

    @application.get("/v1/market/stock-info", response_model=None)
    def stock_info_endpoint(
        request: Request,
        instrument_id: Annotated[str, Query(min_length=1, max_length=200)],
    ) -> Response:
        """Toss's reference data and active buying warnings for one stock."""
        symbol = symbol_of(instrument_id)
        if symbol is None:
            return _error_response(
                request, 404, "instrument_not_found", "instrument is not a listed equity ID"
            )
        source = "toss-kr" if market_of(instrument_id) == "KR" else "toss-us"
        return toss_read(
            request,
            lambda session: stock_info(session, instrument_id, symbol, clock, source),
        )

    @application.get("/v1/market/exchange-rate", response_model=None)
    def exchange_rate_endpoint(
        request: Request,
        base: Annotated[Literal["USD", "KRW"], Query()] = "USD",
        quote: Annotated[Literal["USD", "KRW"], Query()] = "KRW",
    ) -> Response:
        """Toss's reference USD/KRW rate (display only; trades may use another rate)."""
        return toss_read(request, lambda session: exchange_rate(session, base, quote, clock))

    @application.get("/v1/market/broker-calendar", response_model=None)
    def broker_calendar_endpoint(
        request: Request,
        market: Annotated[Literal["US", "KR"], Query()] = "US",
        day: Annotated[date | None, Query(alias="date")] = None,
    ) -> Response:
        """Toss's market hours around `date`, compared with the local US calendar."""
        local = UsEquityCalendar("XNAS")
        return toss_read(
            request, lambda session: market_calendar(session, market, day, local, clock)
        )

    @application.websocket("/v1/realtime")
    async def realtime(websocket: WebSocket) -> None:
        """Relay of the broker's realtime stream. The client sends {"symbols": [...]}.

        Browsers do not apply CORS to WebSockets, so the Origin is checked here: another
        site open in the same browser must not read the user's order events.
        """
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in _cors_origins():
            LOG.warning("refused a realtime connection from origin %r", origin[:200])
            await websocket.close(code=1008)
            return
        await websocket.accept()
        client = hub.add_client(frozenset())

        async def pump() -> None:
            while True:
                await websocket.send_json(await client.queue.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                try:
                    message = await websocket.receive_json()
                except (ValueError, KeyError):
                    continue
                wanted = message.get("symbols") if isinstance(message, dict) else None
                if isinstance(wanted, list):
                    hub.update_client(
                        client,
                        frozenset(
                            str(item).upper()
                            for item in wanted[:MAX_REALTIME_SYMBOLS]
                            if isinstance(item, str) and REALTIME_SYMBOL.fullmatch(item.upper())
                        ),
                    )
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            hub.remove_client(client)

    @application.get("/v1/market/ticks", response_model=None)
    def ticks_endpoint(
        request: Request,
        instrument_id: Annotated[str, Query(min_length=1, max_length=200)],
        session_date: date,
    ) -> Response:
        """Trade ticks received from the broker stream for one stock and New York date."""
        symbol = symbol_of(instrument_id)
        store = ticks()
        if symbol is None or store is None:
            return _error_response(
                request, 404, "ticks_unavailable", "no tick store or unknown instrument"
            )
        source = "toss-kr" if market_of(instrument_id) == "KR" else "toss-us"
        rows = store.read(source, symbol, session_date.isoformat())
        latest = rows[-MAX_TICKS_RETURNED:]
        document = {
            "kind": "tick_list_response",
            "schema_version": "0.1",
            "source_id": source,
            "symbol": symbol,
            "session_date": session_date.isoformat(),
            "completeness": "lossy_stream",
            "count": len(rows),
            "truncated": len(rows) > len(latest),
            "ticks": [
                {
                    key: row[key]
                    for key in (
                        "price",
                        "volume",
                        "currency",
                        "exchange_time_utc",
                        "received_at_utc",
                    )
                }
                for row in latest
            ],
        }
        validate_document(document)
        return JSONResponse(content=document)

    @application.get("/v1/brokers/toss/account", response_model=None)
    def toss_account_endpoint(request: Request) -> Response:
        """Holdings, buying power, commission rates and open orders of every Toss account."""
        return toss_read(request, lambda session: account_snapshot(session, clock))

    @application.get("/v1/brokers/toss/sellable", response_model=None)
    def toss_sellable_endpoint(
        request: Request,
        account_seq: Annotated[int, Query(ge=1, le=1000)],
        symbol: Annotated[str, Query(pattern=r"^[A-Za-z0-9.\-]{1,32}$")],
    ) -> Response:
        return toss_read(
            request,
            lambda session: sellable_quantity(session, account_seq, symbol.upper(), clock),
        )

    @application.post("/v1/brokers/toss/sync", response_model=None)
    def toss_sync_endpoint(request: Request) -> Response:
        """Store the executions of closed, filled US orders in the broker ledger."""
        store = journal.get()
        if store is None:
            return _error_response(
                request,
                503,
                "journal_storage_unconfigured",
                "set SIW_DATA_DIR to keep the broker ledger",
            )
        return toss_read(
            request, lambda session: sync_executions(session, store, instrument_for, clock)
        )

    @application.post("/v1/brokers/kiwoom/sync", response_model=None)
    def kiwoom_sync_endpoint(
        request: Request,
        since: Annotated[date | None, Query()] = None,
        until: Annotated[date | None, Query()] = None,
    ) -> Response:
        """Store the Kiwoom account's filled US orders from `since` (default: a year ago)
        to `until` (default: today); at most 400 days per call."""
        store = journal.get()
        if store is None:
            return _error_response(
                request,
                503,
                "journal_storage_unconfigured",
                "set SIW_DATA_DIR to keep the broker ledger",
            )
        start = since or (
            (until or clock().astimezone(KIWOOM_NEW_YORK).date()) - timedelta(days=365)
        )
        return broker_read(
            request,
            kiwoom,
            "Kiwoom",
            lambda session: kiwoom_sync(session, store, instrument_for, clock, start, until),
        )

    @application.get("/v1/trades/broker", response_model=None)
    def broker_ledger(
        request: Request,
        instrument_id: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int | None, Query(ge=1, le=5000)] = None,
    ) -> Response:
        """The broker API ledger. With `limit`, only the newest executions are listed;
        positions and `execution_count` always cover all of them."""
        store = journal.get()
        if store is None:
            return _error_response(
                request,
                503,
                "journal_storage_unconfigured",
                "set SIW_DATA_DIR to keep the broker ledger",
            )
        executions = store.broker_executions(instrument_id)
        document = {
            "kind": "broker_ledger_response",
            "schema_version": "0.1",
            "ledger": "broker_api",
            "executions": executions if limit is None else executions[-limit:],
            "execution_count": len(executions),
            "positions": account_positions(executions),
        }
        validate_document(document)
        return JSONResponse(content=document)

    @application.get("/v1/research/{dataset}", response_model=None)
    def research_endpoint(
        request: Request,
        dataset: Annotated[str, PathParam(pattern=r"^[a-z_]{1,40}$")],
        key: Annotated[str | None, Query(max_length=64)] = None,
    ) -> Response:
        """A Toss research table; any other query parameters are passed to Toss as given."""
        params = {name: value for name, value in request.query_params.items() if name != "key"}
        return toss_read(
            request, lambda session: research_table(session, dataset, key, params, clock)
        )

    @application.get("/v1/market/quote", response_model=None)
    def market_quote_endpoint(
        request: Request,
        instrument_id: Annotated[str, Query(min_length=1, max_length=200)],
    ) -> Response:
        """Last price, order book, recent trades and price limits, read live from Toss."""
        symbol = symbol_of(instrument_id)
        if symbol is None:
            return _error_response(
                request, 404, "instrument_not_found", "instrument is not a listed equity ID"
            )
        try:
            session = toss()
        except SecretStorageError as exc:
            return _error_response(request, 502, "provider_auth_failed", str(exc))
        if session is None:
            return _error_response(
                request,
                404,
                "quote_source_unavailable",
                "register an enabled real-account Toss key on the Keys screen for live quotes",
            )
        try:
            source = "toss-kr" if market_of(instrument_id) == "KR" else "toss-us"
            document = market_quote(session, instrument_id, symbol, clock, source)
        except InvalidBarsQueryError as exc:
            return _error_response(request, 422, "invalid_market_data_query", str(exc))
        except ProviderAuthError as exc:
            return _error_response(request, 502, "provider_auth_failed", str(exc))
        except ProviderRateLimitError as exc:
            return _error_response(request, 503, "provider_rate_limited", str(exc))
        except ProviderUnavailableError as exc:
            return _error_response(request, 503, "provider_unavailable", str(exc))
        except ProviderDataError as exc:
            return _error_response(request, 502, "provider_data_invalid", str(exc))
        return JSONResponse(content=document, headers={"Cache-Control": "no-store"})

    @application.get("/v1/market/sessions", response_model=None)
    def market_sessions(
        request: Request,
        end_date: date,
        count: Annotated[int, Query(ge=1, le=MAX_SESSION_LIST)] = 1,
        calendar_id: Annotated[Literal["XNAS", "XNYS", "XKRX", "XKOS"], Query()] = "XNAS",
    ) -> Response:
        """The latest `count` trading sessions up to and including `end_date`, oldest first."""
        calendar = UsEquityCalendar(calendar_id)
        sessions: list[dict[str, object]] = []
        day = end_date
        # Holidays and weekends: about 1.5 calendar days per session is enough slack.
        for _ in range(count * 2 + 10):
            if len(sessions) == count:
                break
            try:
                document = calendar.session_window_document(day)
            except CalendarError as exc:
                if sessions:
                    break  # Reached the start of the calendar's range.
                return _error_response(request, 422, "invalid_market_data_query", str(exc))
            if document["is_trading_day"]:
                sessions.append(document)
            day -= timedelta(days=1)
        document = {
            "kind": "session_list_response",
            "schema_version": "0.1",
            "calendar_id": calendar_id,
            "end_date": end_date.isoformat(),
            "sessions": list(reversed(sessions)),
        }
        validate_document(document)
        return JSONResponse(content=document)

    @application.get("/v1/market/sessions/{session_date}", response_model=None)
    def market_session(
        request: Request,
        session_date: date,
        calendar_id: Annotated[Literal["XNAS", "XNYS", "XKRX", "XKOS"], Query()] = "XNAS",
    ) -> Response:
        try:
            document = UsEquityCalendar(calendar_id).session_window_document(session_date)
        except CalendarError as exc:
            return _error_response(request, 422, "invalid_market_data_query", str(exc))
        validate_document(document)
        return JSONResponse(content=document)

    register_journal_routes(
        application,
        journal=journal.get,
        # Configured instruments plus every listed stock the symbol directory knows.
        known_instruments=lambda: (
            {item["instrument"]["instrument_id"] for item in provider.get().instruments()}
            | set(symbols.known())
        ),
        now=clock,
        error_response=_error_response,
    )
    register_credential_routes(
        application,
        credentials=credentials.get,
        active_provider=chosen_provider,
        key_status=provider.key_status,
        allowed_origins=_cors_origins(),
        error_response=_error_response,
    )
    register_import_routes(
        application,
        journal=journal.get,
        symbols=lambda: {
            **{entry.symbol: instrument_id for instrument_id, entry in symbols.known().items()},
            **{
                item["instrument"]["symbol"].upper(): item["instrument"]["instrument_id"]
                for item in provider.get().instruments()
            },
        },
        now=clock,
        error_response=_error_response,
    )

    register_backtest_routes(application, runner=backtests.get, error_response=_error_response)
    register_indicator_routes(
        application, store=custom_indicators.get, error_response=_error_response
    )
    register_agent_routes(
        application,
        store=agents.get,
        get_bars=lambda: MarketBarsService(provider.get()).get_bars,
        journal=journal.get,
        backtests=backtests.get,
        api_key=lambda: _claude_key(credentials.get()),
        now=clock,
        public_sources=sources.get,
        error_response=_error_response,
        ask=agent_ask,
    )

    # Provider and cache I/O are blocking; a sync endpoint runs in FastAPI's threadpool
    # instead of stalling the event loop.
    @application.get("/v1/market/overnight-bars", response_model=None)
    def overnight_bars(
        request: Request,
        instrument_id: Annotated[str, Query(min_length=1, max_length=200)],
        from_date: Annotated[date, Query()],
        to_date: Annotated[date, Query()],
        timeframe: Annotated[str, Query(pattern=r"^[1-9][0-9]{0,2}m$")] = "1m",
        as_of_utc: Annotated[datetime | None, Query()] = None,
    ) -> Response:
        """US day-market (overnight session) bars from Alpaca's BOATS feed, ADR 0030.

        Never merged into /v1/market/bars: a separate stream with its own source label.
        """
        if market_of(instrument_id) != "US":
            return _error_response(
                request, 422, "invalid_market_data_query", "the day market is for US listings"
            )
        if as_of_utc is not None and as_of_utc.utcoffset() is None:
            return _error_response(
                request, 422, "invalid_market_data_query", "as_of_utc must carry a UTC offset"
            )
        return broker_read(
            request,
            boats,
            "Alpaca",
            lambda source: source.get_overnight_bars(
                instrument_id,
                timeframe,
                from_date,
                to_date,
                None if as_of_utc is None else as_of_utc.astimezone(timezone.utc),
            ).to_document(),
        )

    @application.get("/v1/market/bars", response_model=None)
    def market_bars(
        request: Request,
        instrument_id: Annotated[str, Query(min_length=1, max_length=200)],
        start_utc: Annotated[datetime, Query()],
        end_utc: Annotated[datetime, Query()],
        timeframe: Annotated[str, Query(pattern=r"^([1-9][0-9]{0,2}m|1d|1w)$")] = "1m",
        as_of_utc: Annotated[datetime | None, Query()] = None,
    ) -> Response:
        try:
            result = MarketBarsService(provider.get()).get_bars(
                BarsQuery(
                    instrument_id=instrument_id,
                    timeframe=timeframe,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    as_of_utc=as_of_utc,
                )
            )
        except InstrumentNotFoundError:
            return _error_response(
                request,
                404,
                "instrument_not_found",
                "Instrument is not available from the configured provider",
            )
        except InvalidBarsQueryError as exc:
            return _error_response(request, 422, "invalid_market_data_query", str(exc))
        except ProviderAuthError as exc:
            return _error_response(request, 502, "provider_auth_failed", str(exc))
        except ProviderRateLimitError as exc:
            return _error_response(request, 503, "provider_rate_limited", str(exc))
        except ProviderUnavailableError as exc:
            return _error_response(request, 503, "provider_unavailable", str(exc))
        except ProviderDataError as exc:
            return _error_response(request, 502, "provider_data_invalid", str(exc))
        except AggregationError:
            return _error_response(
                request,
                500,
                "market_data_integrity_error",
                "Source bars could not be aggregated under one lineage",
            )
        except StorageError:
            return _error_response(
                request,
                500,
                "market_data_storage_error",
                "Local market data cache could not serve the request",
            )
        return JSONResponse(content=result.to_document())

    return application


app = create_app()

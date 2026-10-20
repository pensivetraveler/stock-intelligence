from __future__ import annotations

import os
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from packages.contracts.validator import validate_document
from services.api.credential_routes import register_credential_routes
from services.api.journal_routes import register_import_routes, register_journal_routes
from services.credentials.pool import PooledProvider
from services.credentials.store import CredentialStore, KeyringSecretBackend, SecretStorageError
from services.journal.store import JournalStore
from services.market_data.aggregation import AggregationError
from services.market_data.bars_service import MarketBarsService
from services.market_data.broker_common import listed_symbol
from services.market_data.calendar import CalendarError, UsEquityCalendar
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
from services.market_data.selection import chosen_provider, provider_from_env
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
)
from services.market_data.toss_info import (
    exchange_rate,
    korean_names,
    market_calendar,
    stock_info,
)
from services.market_data.toss_quotes import market_quote
from services.market_data.toss_session import (
    DEFAULT_BASE_URL as DEFAULT_TOSS_URL,
)
from services.market_data.toss_session import (
    TossCredentials,
    TossSession,
    session_for,
)

APP_NAME = "stock-intelligence-workstation-api"
APP_VERSION = "0.4.0-dev"
API_VERSION = "v1"
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "tauri://localhost",
)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
MAX_SESSION_LIST = 120


def _cors_origins() -> list[str]:
    configured = os.getenv("SIW_CORS_ORIGINS")
    if configured is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


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
    ) -> None:
        self._provider = provider
        self._credentials = credentials
        self._lock = threading.Lock()

    def get(self) -> MarketDataProvider:
        with self._lock:
            if self._provider is None:
                self._provider = _default_market_data_provider(self._credentials())
            return self._provider

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
    ) -> None:
        self._directory = directory
        self._provider = provider
        self._toss = toss
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

    def known(self) -> dict[str, SymbolEntry]:
        directory = self.get()
        return directory.known() if directory is not None else {}


def _toss_session(credentials: CredentialStore | None) -> TossSession | None:
    """The shared session of the first enabled real-account Toss key on the Keys screen."""
    if credentials is None:
        return None
    for record in credentials.list("toss"):
        if record.enabled and record.environment == "real":
            secrets = credentials.secrets(record)
            base_url = os.getenv("SIW_TOSS_BASE_URL", DEFAULT_TOSS_URL).strip().rstrip("/")
            return session_for(
                TossCredentials(secrets["client_id"], secrets["client_secret"], base_url)
            )
    return None


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_app(
    market_data_provider: MarketDataProvider | None = None,
    *,
    journal_store: JournalStore | None = None,
    credential_store: CredentialStore | None = None,
    symbol_directory: SymbolDirectory | None = None,
    toss_session: Callable[[], TossSession | None] | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> FastAPI:
    credentials = _LazyCredentials(credential_store)
    provider = _LazyProvider(market_data_provider, credentials.get)
    toss = toss_session or (lambda: _toss_session(credentials.get()))
    symbols = _LazySymbols(symbol_directory, provider, toss)
    journal = _LazyJournal(journal_store)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        # Under a server, open the fixture and cache at startup so misconfiguration fails
        # fast; clients that skip lifespan events still get the provider on first request.
        provider.get()
        yield

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
        """US-listed stocks whose ticker or company name matches `q`, tickers first."""
        directory = symbols.get()
        if directory is None:
            return _error_response(
                request,
                404,
                "symbol_search_unavailable",
                "the current data provider offers no symbol list, or SIW_DATA_DIR is not set",
            )
        try:
            found = directory.search(q, limit)
        except SymbolDirectoryUnavailableError as exc:
            return _error_response(request, 503, "symbol_directory_unavailable", str(exc))
        document = {
            "kind": "symbol_search_response",
            "schema_version": "0.1",
            "query": q,
            "source_id": directory.source_id,
            "fetched_at_utc": found.fetched_at_utc,
            "stale": found.stale,
            "results": [
                {
                    "listing": directory.listing(entry),
                    "name": entry.name,
                    "local_name": entry.local_name,
                }
                for entry in found.entries
            ],
        }
        validate_document(document)
        return JSONResponse(content=document)

    def toss_read(request: Request, read: Callable[[TossSession], dict[str, object]]) -> Response:
        """Run one on-demand Toss read and map its failures to the API's error envelope."""
        try:
            session = toss()
        except SecretStorageError as exc:
            return _error_response(request, 502, "provider_auth_failed", str(exc))
        if session is None:
            return _error_response(
                request,
                404,
                "quote_source_unavailable",
                "register an enabled real-account Toss key on the Keys screen for live data",
            )
        try:
            document = read(session)
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
        return listed_symbol(instrument_id) or next(
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
                request, 404, "instrument_not_found", "instrument is not a listed US equity ID"
            )
        return toss_read(request, lambda session: stock_info(session, instrument_id, symbol, clock))

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

    @application.get("/v1/market/quote", response_model=None)
    def market_quote_endpoint(
        request: Request,
        instrument_id: Annotated[str, Query(min_length=1, max_length=200)],
    ) -> Response:
        """Last price, order book, recent trades and price limits, read live from Toss."""
        symbol = listed_symbol(instrument_id) or next(
            (
                item["instrument"]["symbol"]
                for item in provider.get().instruments()
                if item["instrument"]["instrument_id"] == instrument_id
            ),
            None,
        )
        if symbol is None:
            return _error_response(
                request, 404, "instrument_not_found", "instrument is not a listed US equity ID"
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
            document = market_quote(session, instrument_id, symbol, clock)
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
        calendar_id: Annotated[Literal["XNAS", "XNYS"], Query()] = "XNAS",
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
        calendar_id: Annotated[Literal["XNAS", "XNYS"], Query()] = "XNAS",
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

    # Provider and cache I/O are blocking; a sync endpoint runs in FastAPI's threadpool
    # instead of stalling the event loop.
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

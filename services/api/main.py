from __future__ import annotations

import os
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
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
from services.api.journal_routes import register_import_routes, register_journal_routes
from services.journal.store import JournalStore
from services.market_data.aggregation import AggregationError
from services.market_data.bars_service import MarketBarsService
from services.market_data.calendar import CalendarError, UsEquityCalendar
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import (
    BarsQuery,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    MarketDataProvider,
)
from services.market_data.storage import (
    CachedMarketDataProvider,
    LocalMarketDataStore,
    StorageError,
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


def _default_market_data_provider() -> MarketDataProvider:
    upstream = FakeMarketDataProvider.from_path()
    configured_data_dir = os.getenv("SIW_DATA_DIR", "").strip()
    if not configured_data_dir:
        return upstream
    return CachedMarketDataProvider(upstream, LocalMarketDataStore(Path(configured_data_dir)))


class _LazyProvider:
    """Build the default provider on first use, so importing the module has no side effects."""

    def __init__(self, provider: MarketDataProvider | None) -> None:
        self._provider = provider
        self._lock = threading.Lock()

    def get(self) -> MarketDataProvider:
        with self._lock:
            if self._provider is None:
                self._provider = _default_market_data_provider()
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_app(
    market_data_provider: MarketDataProvider | None = None,
    *,
    journal_store: JournalStore | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> FastAPI:
    provider = _LazyProvider(market_data_provider)
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
        allow_methods=["GET", "POST"],
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
        known_instruments=lambda: {
            item["instrument"]["instrument_id"] for item in provider.get().instruments()
        },
        now=clock,
        error_response=_error_response,
    )
    register_import_routes(
        application,
        journal=journal.get,
        symbols=lambda: {
            item["instrument"]["symbol"].upper(): item["instrument"]["instrument_id"]
            for item in provider.get().instruments()
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
        timeframe: Annotated[Literal["1m", "5m", "15m", "30m", "60m"], Query()] = "1m",
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

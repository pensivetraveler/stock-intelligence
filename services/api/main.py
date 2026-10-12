from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import (
    BarsQuery,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    MarketDataProvider,
)
from services.market_data.storage import CachedMarketDataProvider, LocalMarketDataStore

APP_NAME = "stock-intelligence-workstation-api"
APP_VERSION = "0.4.0-dev"
API_VERSION = "v1"
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "tauri://localhost",
)


def _cors_origins() -> list[str]:
    configured = os.getenv("SIW_CORS_ORIGINS")
    if configured is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else str(uuid4())


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": _request_id(request),
            }
        },
    )


def _default_market_data_provider() -> MarketDataProvider:
    upstream = FakeMarketDataProvider.from_path()
    configured_data_dir = os.getenv("SIW_DATA_DIR", "").strip()
    if not configured_data_dir:
        return upstream
    return CachedMarketDataProvider(upstream, LocalMarketDataStore(Path(configured_data_dir)))


def create_app(market_data_provider: MarketDataProvider | None = None) -> FastAPI:
    application = FastAPI(title=APP_NAME, version=APP_VERSION)
    provider = market_data_provider or _default_market_data_provider()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept", "Content-Type", "X-Request-ID"],
    )

    @application.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
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

    @application.get("/v1/market/bars", response_model=None)
    async def market_bars(
        request: Request,
        instrument_id: Annotated[str, Query(min_length=1, max_length=200)],
        start_utc: Annotated[datetime, Query()],
        end_utc: Annotated[datetime, Query()],
        timeframe: Annotated[Literal["1m"], Query()] = "1m",
        as_of_utc: Annotated[datetime | None, Query()] = None,
    ) -> Response:
        try:
            result = provider.get_bars(
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
        return JSONResponse(content=result.to_document())

    return application


app = create_app()

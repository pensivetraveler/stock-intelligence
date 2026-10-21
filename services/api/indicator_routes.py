"""Custom Python indicators (ADR 0023)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.backtest.sandbox import StrategyProcess
from services.custom_indicators import runner
from services.custom_indicators.store import CustomIndicatorStore, IndicatorNotFoundError


class SaveRequest(BaseModel):
    code: str = Field(min_length=1, max_length=runner.MAX_CODE_BYTES)
    indicator_id: str | None = Field(default=None, max_length=200)


class PreviewRequest(BaseModel):
    code: str = Field(min_length=1, max_length=runner.MAX_CODE_BYTES)
    params: dict[str, Any] = {}
    bars: list[dict[str, Any]] = Field(min_length=1, max_length=runner.MAX_BARS)
    time_zone: str = Field(default="America/New_York", pattern=r"^[A-Za-z_]+/[A-Za-z_]+$")


class ComputeRequest(BaseModel):
    version: int = Field(ge=1)
    params: dict[str, Any] = {}
    bars: list[dict[str, Any]] = Field(min_length=1, max_length=runner.MAX_BARS)
    time_zone: str = Field(default="America/New_York", pattern=r"^[A-Za-z_]+/[A-Za-z_]+$")


def register_indicator_routes(
    application: FastAPI,
    *,
    store: Callable[[], CustomIndicatorStore | None],
    error_response: ErrorResponse,
    process: runner.Process | None = None,
) -> None:
    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "indicator_storage_unconfigured",
            "Set SIW_DATA_DIR to keep Python indicators; nothing was saved",
        )

    def respond(document: dict[str, Any], status_code: int = 200) -> JSONResponse:
        validate_document(document)
        return JSONResponse(status_code=status_code, content=document)

    sandbox = process or StrategyProcess

    @application.get("/v1/indicators/custom", response_model=None)
    def list_indicators(request: Request) -> Response:
        indicators = store()
        if indicators is None:
            return unavailable(request)
        return respond(
            {
                "kind": "custom_indicator_list_response",
                "schema_version": "0.1",
                "indicators": indicators.indicators(),
            }
        )

    @application.post("/v1/indicators/custom", response_model=None)
    def save_indicator(request: Request, body: SaveRequest) -> Response:
        """Checks the code in the sandbox, then saves it as a new version."""
        indicators = store()
        if indicators is None:
            return unavailable(request)
        try:
            meta = runner.inspect(body.code, sandbox)
            saved = indicators.save(body.code, meta, body.indicator_id)
        except runner.IndicatorError as exc:
            return error_response(request, 422, "invalid_indicator", str(exc))
        except IndicatorNotFoundError:
            return error_response(request, 404, "indicator_not_found", "No such indicator")
        return respond(saved, 201)

    @application.post("/v1/indicators/custom/preview", response_model=None)
    def preview(request: Request, body: PreviewRequest) -> Response:
        """Runs unsaved code on the given bars in the sandbox; nothing is stored."""
        try:
            meta, outputs = runner.preview(
                body.code, body.params, body.bars, body.time_zone, sandbox
            )
        except runner.IndicatorError as exc:
            return error_response(request, 422, "indicator_failed", str(exc))
        return respond(
            {
                "kind": "custom_indicator_preview",
                "schema_version": "0.1",
                **meta,
                "times": [int(bar["time"]) for bar in body.bars],
                "outputs": outputs,
            }
        )

    @application.post("/v1/indicators/custom/{indicator_id}/compute", response_model=None)
    def compute(request: Request, indicator_id: str, body: ComputeRequest) -> Response:
        indicators = store()
        if indicators is None:
            return unavailable(request)
        try:
            found = indicators.indicator(indicator_id, body.version)
        except IndicatorNotFoundError:
            return error_response(request, 404, "indicator_not_found", "No such indicator")
        try:
            outputs = runner.compute(
                found["code"], found, body.params, body.bars, body.time_zone, sandbox
            )
        except runner.IndicatorError as exc:
            return error_response(request, 422, "indicator_failed", str(exc))
        return respond(
            {
                "kind": "custom_indicator_result",
                "schema_version": "0.1",
                "type": found["type"],
                "code_sha256": found["code_sha256"],
                "times": [int(bar["time"]) for bar in body.bars],
                "outputs": outputs,
            }
        )

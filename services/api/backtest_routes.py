"""Backtest strategies and runs (ADR 0021). Fills stay in backtests.sqlite3."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.backtest.jobs import BacktestRunner, RunConfigError
from services.backtest.store import BacktestNotFoundError
from services.backtest.strategy import StrategyError


class StrategyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    definition: dict[str, Any]
    strategy_id: str | None = Field(default=None, max_length=200)


class RunRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=200)
    strategy_version: int | None = Field(default=None, ge=1)
    config: dict[str, Any]


def register_backtest_routes(
    application: FastAPI,
    *,
    runner: Callable[[], BacktestRunner | None],
    error_response: ErrorResponse,
) -> None:
    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "backtest_storage_unconfigured",
            "Set SIW_DATA_DIR to keep backtests; nothing was stored",
        )

    def not_found(request: Request, what: str) -> JSONResponse:
        return error_response(request, 404, "backtest_not_found", f"No such {what}")

    def respond(document: dict[str, Any], status_code: int = 200) -> JSONResponse:
        validate_document(document)
        return JSONResponse(status_code=status_code, content=document)

    @application.get("/v1/backtests/strategies", response_model=None)
    def list_strategies(request: Request) -> Response:
        jobs = runner()
        if jobs is None:
            return unavailable(request)
        return respond(
            {
                "kind": "backtest_strategy_list_response",
                "schema_version": "0.1",
                "strategies": jobs.store.strategies(),
            }
        )

    @application.get("/v1/backtests/strategies/{strategy_id}", response_model=None)
    def get_strategy(
        request: Request, strategy_id: str, version: int | None = Query(default=None, ge=1)
    ) -> Response:
        jobs = runner()
        if jobs is None:
            return unavailable(request)
        try:
            return respond(jobs.store.strategy(strategy_id, version))
        except BacktestNotFoundError:
            return not_found(request, "strategy")

    @application.post("/v1/backtests/strategies", response_model=None)
    def save_strategy(request: Request, body: StrategyRequest) -> Response:
        """A new strategy, or (with strategy_id) its next version."""
        jobs = runner()
        if jobs is None:
            return unavailable(request)
        try:
            saved = jobs.store.save_strategy(body.name.strip(), body.definition, body.strategy_id)
        except StrategyError as exc:
            return error_response(request, 422, "invalid_strategy", str(exc))
        except BacktestNotFoundError:
            return not_found(request, "strategy")
        return respond(saved, 201)

    @application.get("/v1/backtests/runs", response_model=None)
    def list_runs(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> Response:
        jobs = runner()
        if jobs is None:
            return unavailable(request)
        return respond(
            {
                "kind": "backtest_run_list_response",
                "schema_version": "0.1",
                "runs": jobs.store.runs(limit),
            }
        )

    @application.post("/v1/backtests/runs", response_model=None)
    def submit_run(request: Request, body: RunRequest) -> Response:
        jobs = runner()
        if jobs is None:
            return unavailable(request)
        try:
            run = jobs.submit(body.strategy_id, body.strategy_version, body.config)
        except BacktestNotFoundError:
            return not_found(request, "strategy version")
        except RunConfigError as exc:
            return error_response(request, 422, "invalid_backtest_config", str(exc))
        return respond(run, 202)

    @application.get("/v1/backtests/runs/{run_id}", response_model=None)
    def get_run(request: Request, run_id: str) -> Response:
        jobs = runner()
        if jobs is None:
            return unavailable(request)
        try:
            return respond(jobs.store.run(run_id))
        except BacktestNotFoundError:
            return not_found(request, "run")

    @application.get("/v1/backtests/runs/{run_id}/results", response_model=None)
    def get_results(request: Request, run_id: str) -> Response:
        jobs = runner()
        if jobs is None:
            return unavailable(request)
        try:
            results = jobs.store.results(run_id)
            run = jobs.store.run(run_id)
        except BacktestNotFoundError:
            return not_found(request, "run")
        if results is None:
            return error_response(
                request, 409, "backtest_results_unavailable", f"The run is {run['status']}"
            )
        # Stored documents were validated before they were written.
        return JSONResponse(content=results)

    @application.post("/v1/backtests/runs/{run_id}/cancel", response_model=None)
    def cancel_run(request: Request, run_id: str) -> Response:
        jobs = runner()
        if jobs is None:
            return unavailable(request)
        try:
            return respond(jobs.cancel(run_id))
        except BacktestNotFoundError:
            return not_found(request, "run")

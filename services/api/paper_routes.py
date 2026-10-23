"""Paper-trading views and controls for SIW Agent (ADR 0037 §6). No order endpoint here.

Orders are sent only by the resident runner process. The API reads its ledger and writes
approvals and the kill switch:

- Stopping is always allowed: `kill` and `disarm` accept any caller.
- Starting needs the SIW Agent app: `approvals`, `arm` and `release` answer 403 unless
  the request comes from the Tauri origin. A local process can forge the header, so this
  keeps web pages and the Research app out; it is not an authentication boundary.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.backtest.strategy import StrategyError
from services.credentials.store import CredentialStore, SecretStorageError
from services.paper_trading.intent import FxRate, StrategyRef
from services.paper_trading.limits import PaperLimits
from services.paper_trading.runner import InstrumentPlan, RunPlan
from services.paper_trading.service import kiwoom_account_ref
from services.paper_trading.signals import rule_signals
from services.paper_trading.store import PaperOrderStore, PaperStoreError

AGENT_ORIGIN = "tauri://localhost"
# Kiwoom paper orders cover US listings with a Kiwoom exchange code (ADR 0037 P2).
US_INSTRUMENT = re.compile(r"^us:[a-z][a-z0-9.]{0,9}:(xnas|xnys|xase):2026$")
StrategyLookup = Callable[[str, int], dict[str, Any]]


class InstrumentRequest(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=200)
    symbol: str = Field(min_length=1, max_length=32)


class ApprovalRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=200)
    strategy_version: int = Field(ge=1)
    instruments: list[InstrumentRequest] = Field(min_length=1, max_length=50)
    credential_id: str = Field(min_length=1, max_length=200)
    max_order_amount_krw: str | None = "1000000"
    max_daily_orders: int | None = Field(default=None, ge=1)
    max_daily_loss_rate: str | None = None
    max_position_amount_krw: str | None = None
    market_buffer_bps: str = "50"
    fee_rate: str = "0"
    krw_per_usd: str | None = None
    fx_source: str | None = Field(default=None, max_length=200)
    equity_basis: str | None = None
    max_signal_age_seconds: int = Field(default=300, ge=10, le=86_400)


class ArmRequest(BaseModel):
    approval_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class KillRequest(BaseModel):
    reason: str = Field(default="kill switch pressed", min_length=1, max_length=200)


def _decimal(value: str | None, what: str) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{what} must be a decimal number") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{what} must be a non-negative number")
    return number


def approval_document(
    approval_id: str, approved_at: str, content: dict[str, Any]
) -> dict[str, Any]:
    return {
        "kind": "paper_approval",
        "schema_version": "0.1",
        "approval_id": approval_id,
        "approved_at_utc": approved_at,
        **content,
    }


def register_paper_routes(
    application: FastAPI,
    *,
    store: Callable[[], PaperOrderStore | None],
    credentials: Callable[[], CredentialStore | None],
    strategies: Callable[[], StrategyLookup | None],
    now: Callable[[], datetime],
    error_response: ErrorResponse,
) -> None:
    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request, 503, "paper_storage_unconfigured", "Set SIW_DATA_DIR to use paper trading"
        )

    def from_agent(request: Request) -> bool:
        return request.headers.get("origin") == AGENT_ORIGIN

    def forbidden(request: Request) -> JSONResponse:
        return error_response(
            request, 403, "agent_app_only", "Only the SIW Agent app can start paper trading"
        )

    def respond(document: dict[str, Any], status_code: int = 200) -> JSONResponse:
        validate_document(document)
        return JSONResponse(status_code=status_code, content=document)

    @application.get("/v1/paper/status", response_model=None)
    def paper_status(request: Request) -> Response:
        paper = store()
        return unavailable(request) if paper is None else respond(paper.status())

    @application.get("/v1/paper/approvals", response_model=None)
    def list_approvals(request: Request) -> Response:
        paper = store()
        if paper is None:
            return unavailable(request)
        approvals = [approval_document(*row) for row in paper.latest_approvals()]
        return respond(
            {
                "kind": "paper_approval_list_response",
                "schema_version": "0.1",
                "approvals": approvals,
            }
        )

    @application.get("/v1/paper/intents", response_model=None)
    def list_intents(request: Request, limit: int = Query(100, ge=1, le=500)) -> Response:
        paper = store()
        if paper is None:
            return unavailable(request)
        return respond(
            {
                "kind": "paper_intent_list_response",
                "schema_version": "0.1",
                "intents": paper.intents(limit),
            }
        )

    @application.get("/v1/paper/executions", response_model=None)
    def list_executions(request: Request, limit: int = Query(200, ge=1, le=1000)) -> Response:
        paper = store()
        if paper is None:
            return unavailable(request)
        return respond(
            {
                "kind": "paper_execution_list_response",
                "schema_version": "0.1",
                "executions": paper.executions(limit),
            }
        )

    @application.post("/v1/paper/approvals", response_model=None)
    def approve(request: Request, body: ApprovalRequest) -> Response:
        if not from_agent(request):
            return forbidden(request)
        paper, keys, lookup = store(), credentials(), strategies()
        if paper is None or keys is None or lookup is None:
            return unavailable(request)
        try:
            strategy = lookup(body.strategy_id, body.strategy_version)
        except LookupError:
            return error_response(request, 404, "strategy_not_found", "No such strategy version")
        instruments = []
        for item in body.instruments:
            if not US_INSTRUMENT.fullmatch(item.instrument_id):
                return error_response(
                    request,
                    422,
                    "instrument_not_supported",
                    f"{item.instrument_id} is not a US listing Kiwoom paper orders can reach",
                )
            instruments.append(
                InstrumentPlan(item.instrument_id, item.symbol.upper(), "America/New_York")
            )
        try:
            # The runner refuses what it cannot run; refuse it here, before approval.
            rule_signals(strategy["definition"], instruments[0].instrument_id, "UTC", [], False)
        except StrategyError as exc:
            return error_response(request, 422, "strategy_not_supported", str(exc))
        record = next(
            (r for r in keys.list("kiwoom") if r.credential_id == body.credential_id), None
        )
        if record is None or not record.enabled or record.environment != "demo":
            return error_response(
                request, 422, "not_a_demo_key", "Choose an enabled Kiwoom key registered as demo"
            )
        try:
            account_ref = kiwoom_account_ref(keys.secrets(record)["app_key"])
        except SecretStorageError as exc:
            return error_response(request, 422, "key_unreadable", str(exc))
        try:
            krw_per_usd = _decimal(body.krw_per_usd, "krw_per_usd")
            plan = RunPlan(
                strategy=StrategyRef(
                    strategy["strategy_id"],
                    int(strategy["version"]),
                    strategy["definition_sha256"],
                    "rules",
                ),
                definition=strategy["definition"],
                instruments=tuple(instruments),
                limits=PaperLimits(
                    allowed_instrument_ids=tuple(i.instrument_id for i in instruments),
                    max_order_amount_krw=_decimal(
                        body.max_order_amount_krw, "max_order_amount_krw"
                    ),
                    max_daily_orders=body.max_daily_orders,
                    max_daily_loss_rate=_decimal(body.max_daily_loss_rate, "max_daily_loss_rate"),
                    max_position_amount_krw=_decimal(
                        body.max_position_amount_krw, "max_position_amount_krw"
                    ),
                ),
                broker_id="kiwoom",
                broker_account_ref=account_ref,
                market_buffer_bps=_decimal(body.market_buffer_bps, "market_buffer_bps")
                or Decimal(0),
                fee_rate=_decimal(body.fee_rate, "fee_rate") or Decimal(0),
                fx=None
                if krw_per_usd is None
                else FxRate(krw_per_usd, body.fx_source or "confirmed at approval", now()),
                equity_basis=_decimal(body.equity_basis, "equity_basis"),
                max_signal_age_seconds=body.max_signal_age_seconds,
            )
        except ValueError as exc:
            return error_response(request, 422, "invalid_approval", str(exc))
        content = plan.approval_content()
        approval_id = paper.record_approval(content)
        stored = next(row for row in paper.latest_approvals(500) if row[0] == approval_id)
        return respond(approval_document(*stored), 201)

    @application.post("/v1/paper/arm", response_model=None)
    def arm(request: Request, body: ArmRequest) -> Response:
        if not from_agent(request):
            return forbidden(request)
        paper = store()
        if paper is None:
            return unavailable(request)
        try:
            paper.arm(body.approval_id, source="agent_app")
        except PaperStoreError as exc:
            return error_response(request, 409, "cannot_arm", str(exc))
        return respond(paper.status())

    @application.post("/v1/paper/release", response_model=None)
    def release(request: Request) -> Response:
        if not from_agent(request):
            return forbidden(request)
        paper = store()
        if paper is None:
            return unavailable(request)
        paper.release(source="agent_app")
        return respond(paper.status())

    @application.post("/v1/paper/disarm", response_model=None)
    def disarm(request: Request) -> Response:
        paper = store()
        if paper is None:
            return unavailable(request)
        paper.disarm(source="api")
        return respond(paper.status())

    @application.post("/v1/paper/kill", response_model=None)
    def kill(request: Request, body: KillRequest | None = None) -> Response:
        paper = store()
        if paper is None:
            return unavailable(request)
        paper.halt((body or KillRequest()).reason, source="api")
        return respond(paper.status())

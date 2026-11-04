"""Real-account order views and controls for SIW Agent (ADR 0047). No order endpoint here.

Orders are sent only by the resident live runner, and only after the user confirmed each
one here. As for paper trading:

- Stopping is always allowed: `kill`, `disarm` and declining an order accept any caller.
- Starting needs the SIW Agent app: `approvals`, `arm`, `release` and confirming an order
  answer 403 unless the request comes from the Tauri origin. A local process can forge
  the header, so this keeps web pages and the Research app out; it is not authentication.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.api.paper_routes import (
    AGENT_ORIGIN,
    US_INSTRUMENT,
    ArmRequest,
    InstrumentRequest,
    KillRequest,
    StrategyLookup,
    _decimal,
    approval_document,
)
from services.backtest.protection import ProtectionRules
from services.backtest.strategy import StrategyError, validate_definition
from services.credentials.store import CredentialStore, SecretStorageError
from services.live_trading.kiwoom_live import live_account_ref
from services.live_trading.store import LiveOrderStore
from services.paper_trading.intent import StrategyRef
from services.paper_trading.limits import PaperLimits
from services.paper_trading.runner import InstrumentPlan, RunPlan
from services.paper_trading.signals import rule_signals
from services.paper_trading.store import PaperStoreError

INTENT_ID = Path(pattern=r"^[a-f0-9]{64}$")


class LiveApprovalRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=200)
    strategy_version: int = Field(ge=1)
    # One instrument while each order is confirmed by hand (ADR 0047).
    instruments: list[InstrumentRequest] = Field(min_length=1, max_length=1)
    credential_id: str = Field(min_length=1, max_length=200)
    max_order_amount_usd: str
    max_daily_orders: int = Field(ge=1, le=20)
    market_buffer_bps: str = "50"
    fee_rate: str = "0"
    max_signal_age_seconds: int = Field(default=300, ge=10, le=86_400)
    bar_settle_seconds: int | None = Field(default=None, ge=0, le=900)
    unfilled_cancel_seconds: int | None = Field(default=None, ge=10, le=3_600)
    confirm_seconds: int = Field(default=60, ge=10, le=600)


def register_live_routes(
    application: FastAPI,
    *,
    store: Callable[[], LiveOrderStore | None],
    credentials: Callable[[], CredentialStore | None],
    strategies: Callable[[], StrategyLookup | None],
    now: Callable[[], datetime],
    error_response: ErrorResponse,
) -> None:
    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request, 503, "live_storage_unconfigured", "Set SIW_DATA_DIR to use live orders"
        )

    def from_agent(request: Request) -> bool:
        return request.headers.get("origin") == AGENT_ORIGIN

    def forbidden(request: Request) -> JSONResponse:
        return error_response(
            request, 403, "agent_app_only", "Only the SIW Agent app can start live orders"
        )

    def respond(document: dict[str, Any], status_code: int = 200) -> JSONResponse:
        validate_document(document)
        return JSONResponse(status_code=status_code, content=document)

    def with_store(
        request: Request, action: Callable[[LiveOrderStore], JSONResponse]
    ) -> JSONResponse:
        live = store()
        return unavailable(request) if live is None else action(live)

    @application.get("/v1/live/status", response_model=None)
    def live_status(request: Request) -> Response:
        return with_store(request, lambda live: respond(live.status()))

    @application.get("/v1/live/approvals", response_model=None)
    def list_approvals(request: Request) -> Response:
        return with_store(
            request,
            lambda live: respond(
                {
                    "kind": "paper_approval_list_response",
                    "schema_version": "0.1",
                    "approvals": [approval_document(*row) for row in live.latest_approvals()],
                }
            ),
        )

    @application.get("/v1/live/intents", response_model=None)
    def list_intents(request: Request, limit: int = Query(100, ge=1, le=500)) -> Response:
        return with_store(
            request,
            lambda live: respond(
                {
                    "kind": "paper_intent_list_response",
                    "schema_version": "0.1",
                    "intents": live.intents(limit),
                }
            ),
        )

    @application.get("/v1/live/executions", response_model=None)
    def list_executions(request: Request, limit: int = Query(200, ge=1, le=1000)) -> Response:
        return with_store(
            request,
            lambda live: respond(
                {
                    "kind": "paper_execution_list_response",
                    "schema_version": "0.1",
                    "executions": live.executions(limit),
                }
            ),
        )

    @application.get("/v1/live/confirmations", response_model=None)
    def list_confirmations(request: Request) -> Response:
        return with_store(
            request,
            lambda live: respond(
                {
                    "kind": "live_confirmation_list_response",
                    "schema_version": "0.1",
                    "now_utc": now().isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "confirmations": live.open_confirmations(),
                }
            ),
        )

    def decide(request: Request, intent_id: str, decision: str) -> JSONResponse:
        def action(live: LiveOrderStore) -> JSONResponse:
            try:
                live.decide_confirmation(intent_id, decision, source="agent_app")
            except PaperStoreError as exc:
                return error_response(request, 409, "cannot_decide", str(exc))
            return list_confirmations(request)  # type: ignore[return-value]

        return with_store(request, action)

    @application.post("/v1/live/intents/{intent_id}/confirm", response_model=None)
    def confirm(request: Request, intent_id: str = INTENT_ID) -> Response:
        if not from_agent(request):
            return forbidden(request)
        return decide(request, intent_id, "confirmed")

    @application.post("/v1/live/intents/{intent_id}/decline", response_model=None)
    def decline(request: Request, intent_id: str = INTENT_ID) -> Response:
        return decide(request, intent_id, "declined")

    @application.post("/v1/live/approvals", response_model=None)
    def approve(request: Request, body: LiveApprovalRequest) -> Response:
        if not from_agent(request):
            return forbidden(request)
        live, keys, lookup = store(), credentials(), strategies()
        if live is None or keys is None or lookup is None:
            return unavailable(request)
        try:
            strategy = lookup(body.strategy_id, body.strategy_version)
        except LookupError:
            return error_response(request, 404, "strategy_not_found", "No such strategy version")
        item = body.instruments[0]
        if not US_INSTRUMENT.fullmatch(item.instrument_id):
            return error_response(
                request,
                422,
                "instrument_not_supported",
                f"{item.instrument_id} is not a US listing Kiwoom orders can reach",
            )
        instruments = (InstrumentPlan(item.instrument_id, item.symbol.upper(), "America/New_York"),)
        try:
            rule_signals(strategy["definition"], item.instrument_id, "UTC", [], False)
        except StrategyError as exc:
            return error_response(request, 422, "strategy_not_supported", str(exc))
        if (
            ProtectionRules.of(validate_definition(strategy["definition"])).active
            and body.unfilled_cancel_seconds is None
        ):
            return error_response(
                request,
                422,
                "protection_needs_cancel",
                "A strategy with stops needs a time to cancel unfilled orders",
            )
        record = next(
            (r for r in keys.list("kiwoom") if r.credential_id == body.credential_id), None
        )
        if record is None or not record.enabled or record.environment != "real":
            return error_response(
                request, 422, "not_a_real_key", "Choose an enabled Kiwoom key registered as real"
            )
        try:
            account_ref = live_account_ref(keys.secrets(record)["app_key"])
        except SecretStorageError as exc:
            return error_response(request, 422, "key_unreadable", str(exc))
        try:
            cap = _decimal(body.max_order_amount_usd, "max_order_amount_usd")
            if cap is None or cap <= 0:
                raise ValueError("max_order_amount_usd must be above zero")
            plan = RunPlan(
                strategy=StrategyRef(
                    strategy["strategy_id"],
                    int(strategy["version"]),
                    strategy["definition_sha256"],
                    "rules",
                ),
                definition=strategy["definition"],
                instruments=instruments,
                limits=PaperLimits(
                    allowed_instrument_ids=(item.instrument_id,),
                    max_daily_orders=body.max_daily_orders,
                    max_order_amount_usd=cap,
                ),
                broker_id="kiwoom",
                broker_account_ref=account_ref,
                market_buffer_bps=_decimal(body.market_buffer_bps, "market_buffer_bps")
                or Decimal(0),
                fee_rate=_decimal(body.fee_rate, "fee_rate") or Decimal(0),
                max_signal_age_seconds=body.max_signal_age_seconds,
                bar_settle_seconds=body.bar_settle_seconds,
                unfilled_cancel_seconds=body.unfilled_cancel_seconds,
                confirm_seconds=body.confirm_seconds,
            )
        except ValueError as exc:
            return error_response(request, 422, "invalid_approval", str(exc))
        approval_id = live.record_approval(plan.approval_content())
        stored = next(row for row in live.latest_approvals(500) if row[0] == approval_id)
        return respond(approval_document(*stored), 201)

    @application.post("/v1/live/arm", response_model=None)
    def arm(request: Request, body: ArmRequest) -> Response:
        if not from_agent(request):
            return forbidden(request)

        def action(live: LiveOrderStore) -> JSONResponse:
            try:
                live.arm(body.approval_id, source="agent_app")
            except PaperStoreError as exc:
                return error_response(request, 409, "cannot_arm", str(exc))
            return respond(live.status())

        return with_store(request, action)

    @application.post("/v1/live/release", response_model=None)
    def release(request: Request) -> Response:
        if not from_agent(request):
            return forbidden(request)

        def action(live: LiveOrderStore) -> JSONResponse:
            live.release(source="agent_app")
            return respond(live.status())

        return with_store(request, action)

    @application.post("/v1/live/disarm", response_model=None)
    def disarm(request: Request) -> Response:
        def action(live: LiveOrderStore) -> JSONResponse:
            live.disarm(source="api")
            return respond(live.status())

        return with_store(request, action)

    @application.post("/v1/live/kill", response_model=None)
    def kill(request: Request, body: KillRequest | None = None) -> Response:
        def action(live: LiveOrderStore) -> JSONResponse:
            live.halt((body or KillRequest()).reason, source="api")
            return respond(live.status())

        return with_store(request, action)

"""Read routes for Kiwoom's US condition search (조건검색, ADR 0048). Nothing is stored.

Only the conditions named in CONNECTED_CONDITIONS are listed or run. They are found by
name each time, because Kiwoom renumbers its conditions when one is added above them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from packages.contracts.validator import validate_document
from services.market_data.broker_common import utc_text
from services.market_data.kiwoom_conditions import (
    CONNECTED_CONDITIONS,
    Connect,
    KiwoomConditionSearch,
    default_connect,
)

SOURCE_ID = "kiwoom-us-conditions"
BrokerRead = Callable[
    [Request, Callable[[], Any], str, Callable[[Any], dict[str, object]]], Response
]
ErrorResponse = Callable[[Request, int, str, str], JSONResponse]


def register_screener_routes(
    application: FastAPI,
    *,
    kiwoom_session: Callable[[], Any],
    listing_for: Callable[[str], dict[str, Any] | None],
    broker_read: BrokerRead,
    error_response: ErrorResponse,
    clock: Callable[[], datetime],
    connect: Connect | None = None,
    connected: tuple[str, ...] = CONNECTED_CONDITIONS,
) -> None:
    def search_of(session: Any) -> KiwoomConditionSearch:
        return KiwoomConditionSearch(
            session.access_token,
            "demo" if session.environment == "demo" else "real",
            connect=connect or default_connect,
        )

    def header(kind: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "schema_version": "0.1",
            "broker": "kiwoom",
            "market": "US",
            "source_id": SOURCE_ID,
        }

    @application.get("/v1/screeners/kiwoom/us/conditions", response_model=None)
    def kiwoom_conditions(request: Request) -> Response:
        """The connected conditions as saved in 영웅문Global now, and any that are not."""

        def read(session: Any) -> dict[str, object]:
            saved = {item.name: item.seq for item in search_of(session).conditions()}
            document = {
                **header("broker_condition_list"),
                "fetched_at_utc": utc_text(clock()),
                "conditions": [
                    {"seq": saved[name], "name": name} for name in connected if name in saved
                ],
                "missing": [name for name in connected if name not in saved],
            }
            validate_document(document)
            return document

        return broker_read(request, kiwoom_session, "Kiwoom", read)

    @application.post("/v1/screeners/kiwoom/us/conditions/run", response_model=None)
    def kiwoom_condition_run(
        request: Request,
        name: Annotated[str, Query(min_length=1, max_length=200)],
    ) -> Response:
        """One run of a connected condition: Kiwoom's figures at the time of the run."""
        if name not in connected:
            return error_response(
                request,
                422,
                "condition_not_connected",
                f"SIW runs only these Kiwoom conditions: {', '.join(connected)}",
            )

        def read(session: Any) -> dict[str, object]:
            run = search_of(session).run(name)
            document = {
                **header("broker_condition_result"),
                "fetched_at_utc": utc_text(clock()),
                "condition": {"seq": run.condition.seq, "name": run.condition.name},
                "complete": run.complete,
                "rows": [
                    {
                        "symbol": row.symbol,
                        "name": row.name,
                        "kiwoom_exchange": row.kiwoom_exchange,
                        "sector": row.sector,
                        "listing": listing_for(row.symbol),
                        **row.numbers,
                    }
                    for row in run.rows
                ],
            }
            validate_document(document)
            return document

        return broker_read(request, kiwoom_session, "Kiwoom", read)

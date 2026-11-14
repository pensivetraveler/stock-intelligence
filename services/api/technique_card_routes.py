"""Routes for technique sources and cards (ADR 0056 L1 + L2).

- `GET  /v1/technique-sources`                      library items tagged 매매기법 (L1)
- `POST /v1/technique-sources/preview`              fetch + recheck, what would be sent
- `POST /v1/technique-cards`                        start an extraction (the user's button)
- `GET  /v1/technique-cards/jobs/{job_id}`          extraction state
- `GET  /v1/technique-cards`                        stored cards (summaries)
- `GET  /v1/technique-cards/{card_id}`              card, checks, resolutions, timeline
- `POST /v1/technique-cards/{card_id}/resolutions`  the user's confirm / edit / drop + answers
- `POST /v1/technique-cards/{card_id}/recheck`      library recheck before reuse

The extraction runs in a background thread (a long source takes minutes); one at a time per
item. Error messages never carry transcript text. These documents are local to the app and
not part of the shared contracts schema yet.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Literal

import jsonschema
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from services.api.journal_routes import ErrorResponse
from services.technique_cards.extract import ExtractionError, Keys
from services.technique_cards.library import LibraryError
from services.technique_cards.service import SourceWithdrawnError, TechniqueCards
from services.technique_cards.store import CardNotFoundError
from services.technique_cards.structured_schema import STRUCTURED_SCHEMA

ITEM_REF = r"^kl://[a-z0-9][a-z0-9-]{0,62}/item/[0-9A-HJKMNP-TV-Z]{26}$"
_CONDITION_VALIDATOR = jsonschema.Draft202012Validator(
    {**STRUCTURED_SCHEMA["$defs"]["condition"], "$defs": STRUCTURED_SCHEMA["$defs"]}
)


class ItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    item_ref: str = Field(pattern=ITEM_REF)
    # The assigned sonnet unless the user picks another model for this extraction.
    model: Literal["claude-sonnet-5-5", "google/gemini-4-argon", "openai/gpt-6-luna"] = (
        "claude-sonnet-5-5"
    )


class ConditionResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm", "edit", "drop"]
    condition: dict[str, Any] | None = None
    note: str = Field(default="", max_length=2_000)


class ResolutionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    conditions: dict[str, ConditionResolution] = Field(default_factory=dict)
    ambiguities: dict[str, str] = Field(default_factory=dict)


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "card_id",
            "item_ref",
            "title",
            "url",
            "version",
            "usage",
            "recheck_outcome",
            "rechecked_at",
            "withdrawn",
            "ready",
            "blocks",
            "corrected",
            "created_at",
            "updated_at",
        )
    } | {"flags": row["checks"]["counts"]}


def register_technique_card_routes(
    application: FastAPI,
    *,
    cards: Callable[[], TechniqueCards | None],
    keys: Callable[[], Keys],
    error_response: ErrorResponse,
    start: Callable[[Callable[[], None]], None] | None = None,
) -> None:
    running = threading.Lock()

    def launch(work: Callable[[], None]) -> None:
        if start is not None:
            start(work)
        else:
            threading.Thread(target=work, name="technique-card", daemon=True).start()

    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "technique_library_unconfigured",
            "Set SIW_DATA_DIR, SIW_KL_ROOT, SIW_KL_TASK and SIW_KL_TAGSUBSCRIPTION to read"
            " technique sources from the knowledge library",
        )

    def library_error(request: Request, exc: LibraryError) -> JSONResponse:
        return error_response(request, 502, "technique_library_refused", str(exc))

    @application.get("/v1/technique-sources", response_model=None)
    def list_sources(request: Request, refresh: bool = False) -> Response:
        service = cards()
        if service is None:
            return unavailable(request)
        try:
            sources = service.sources(refresh)
        except LibraryError as exc:
            return library_error(request, exc)
        by_item: dict[str, list[str]] = {}
        for row in service.store.list():
            by_item.setdefault(row["item_ref"], []).append(row["card_id"])
        return JSONResponse(
            {
                "kind": "technique_sources",
                "sources": [
                    s.to_json() | {"card_ids": by_item.get(s.item_ref, [])} for s in sources
                ],
            }
        )

    @application.post("/v1/technique-sources/preview", response_model=None)
    def preview(request: Request, body: ItemRequest) -> Response:
        service = cards()
        if service is None:
            return unavailable(request)
        try:
            source = service.library.source(body.item_ref)
            if source is None:
                return error_response(
                    request, 404, "technique_source_not_found", "No current item with that tag"
                )
            return JSONResponse(
                {"kind": "technique_source_preview", **service.preview(source, body.model)}
            )
        except SourceWithdrawnError as exc:
            return error_response(request, 409, "technique_source_withdrawn", str(exc))
        except LibraryError as exc:
            return library_error(request, exc)

    @application.post("/v1/technique-cards", response_model=None)
    def start_extraction(request: Request, body: ItemRequest) -> Response:
        service = cards()
        if service is None:
            return unavailable(request)
        with running:
            existing = service.store.running_job(body.item_ref)
            if existing is not None:
                return JSONResponse(
                    status_code=202, content={"kind": "technique_card_job", "job_id": existing}
                )
            try:
                source = service.library.source(body.item_ref)
            except LibraryError as exc:
                return library_error(request, exc)
            if source is None:
                return error_response(
                    request, 404, "technique_source_not_found", "No current item with that tag"
                )
            job_id = service.store.new_job(body.item_ref)
        current_keys = keys()

        def work() -> None:
            card_id, error, attempts = None, "", []
            try:
                card_id = service.extract(source, current_keys, model=body.model)
            except ExtractionError as exc:
                error, attempts = str(exc), exc.attempts
            except (SourceWithdrawnError, LibraryError) as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 - the job must end, and say why
                error = f"unexpected {type(exc).__name__}"
            service.store.finish_job(job_id, card_id, error, attempts)

        launch(work)
        return JSONResponse(
            status_code=202, content={"kind": "technique_card_job", "job_id": job_id}
        )

    @application.get("/v1/technique-cards/jobs/{job_id}", response_model=None)
    def job(request: Request, job_id: str) -> Response:
        service = cards()
        if service is None:
            return unavailable(request)
        found = service.store.job(job_id)
        if found is None:
            return error_response(request, 404, "technique_card_job_not_found", "No such job")
        return JSONResponse({"kind": "technique_card_job", **found})

    @application.get("/v1/technique-cards", response_model=None)
    def list_cards(request: Request) -> Response:
        service = cards()
        if service is None:
            return unavailable(request)
        return JSONResponse(
            {"kind": "technique_cards", "cards": [_summary(r) for r in service.store.list()]}
        )

    @application.get("/v1/technique-cards/{card_id}", response_model=None)
    def get_card(request: Request, card_id: str) -> Response:
        service = cards()
        if service is None:
            return unavailable(request)
        try:
            return JSONResponse({"kind": "technique_card", **service.card(card_id)})
        except CardNotFoundError:
            return error_response(request, 404, "technique_card_not_found", "No such card")

    @application.post("/v1/technique-cards/{card_id}/resolutions", response_model=None)
    def resolve(request: Request, card_id: str, body: ResolutionsRequest) -> Response:
        service = cards()
        if service is None:
            return unavailable(request)
        try:
            row = service.store.get(card_id)
        except CardNotFoundError:
            return error_response(request, 404, "technique_card_not_found", "No such card")
        count = len(row["card"].get("conditions") or [])
        ambiguity_count = len(row["card"].get("ambiguities") or [])
        conditions: dict[str, dict[str, Any]] = {}
        for key, resolution in body.conditions.items():
            if not key.isdigit() or int(key) >= count:
                return error_response(
                    request, 422, "invalid_technique_resolution", f"No condition {key}"
                )
            if resolution.action == "edit":
                errors = list(_CONDITION_VALIDATOR.iter_errors(resolution.condition))
                if resolution.condition is None or errors:
                    return error_response(
                        request,
                        422,
                        "invalid_technique_resolution",
                        f"Condition {key}: an edit needs a condition matching the card schema",
                    )
                conditions[key] = resolution.model_dump() | {
                    "condition": resolution.condition | {"basis": "user"}
                }
            else:
                conditions[key] = resolution.model_dump() | {"condition": None}
        for key, answer in body.ambiguities.items():
            if not key.isdigit() or int(key) >= ambiguity_count or len(answer) > 2_000:
                return error_response(
                    request, 422, "invalid_technique_resolution", f"No ambiguity {key}"
                )
        try:
            updated = service.store.resolve(card_id, body.version, conditions, body.ambiguities)
        except ValueError as exc:
            return error_response(request, 409, "technique_card_version_conflict", str(exc))
        return JSONResponse({"kind": "technique_card", **_summary(updated)})

    @application.post("/v1/technique-cards/{card_id}/recheck", response_model=None)
    def recheck(request: Request, card_id: str) -> Response:
        service = cards()
        if service is None:
            return unavailable(request)
        try:
            return JSONResponse({"kind": "technique_card", **_summary(service.recheck(card_id))})
        except CardNotFoundError:
            return error_response(request, 404, "technique_card_not_found", "No such card")

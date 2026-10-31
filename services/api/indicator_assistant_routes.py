"""Routes for the Pine indicator assistant (ADR 0042, indicators steps 1 and 2).

Conversations live in the review store's conversation tables under `indicator:<id>`, next
to the model setting. Claude's scripts are run by the app, which posts each result back.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from starlette.responses import Response

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.api.review_routes import MAX_IMAGE_BYTES, PNG_MAGIC
from services.assistant.indicator import (
    DEFAULT_MODEL,
    KEY_PREFIX,
    MAX_CODE,
    MAX_REFERENCE,
    MAX_RUNS,
    RETRY_MODEL,
    IndicatorConversation,
    call_indicator_claude,
    system_prompt,
)
from services.review.claude import MODELS
from services.review.conversation import ConversationError
from services.review.store import ConversationNotFoundError, ReviewStore

Model = Literal["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
SETTING = "indicator_model"


class ChartContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(min_length=1, max_length=20)
    bars: int = Field(ge=0, le=1_000_000)
    code: str | None = Field(default=None, max_length=MAX_CODE)


class ChartImage(BaseModel):
    """A picture of the bars in view, attached only when the investor chooses to."""

    model_config = ConfigDict(extra="forbid")
    png_base64: str = Field(min_length=8, max_length=MAX_IMAGE_BYTES * 2)
    bars: int = Field(ge=1, le=1_000_000)
    from_utc: AwareDatetime
    to_utc: AwareDatetime


class Origin(BaseModel):
    """What the first request starts from. The code itself travels as the editor's code."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["saved", "tradingview", "builtin"]
    detail: str | None = Field(default=None, max_length=4000)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    language: Literal["en", "ko"] = "ko"
    reference: str = Field(min_length=1, max_length=MAX_REFERENCE)
    context: ChartContext
    origin: Origin | None = None
    image: ChartImage | None = None


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    context: ChartContext
    # Only for a retry (Sonnet at high effort, then Opus): this request alone uses them.
    model: Model | None = None
    effort: Literal["medium", "high"] | None = None
    image: ChartImage | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_use_id: str = Field(min_length=1, max_length=200)
    ok: bool
    error: str | None = Field(default=None, max_length=2000)
    summary: str | None = Field(default=None, max_length=8000)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Model


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _image(image: ChartImage | None) -> dict[str, Any] | None:
    """The attachment as the conversation keeps it; ValueError unless it is a real PNG."""
    if image is None:
        return None
    try:
        raw = base64.b64decode(image.png_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("not base64") from exc
    if not raw.startswith(PNG_MAGIC) or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("not a PNG of an allowed size")
    return {
        "png_base64": image.png_base64,
        "bars": image.bars,
        "from_utc": _utc(image.from_utc),
        "to_utc": _utc(image.to_utc),
    }


def register_indicator_assistant_routes(
    application: FastAPI,
    *,
    store: Callable[[], ReviewStore | None],
    error_response: ErrorResponse,
    api_key: Callable[[], str | None] = lambda: None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    converse: Callable[..., dict[str, Any]] = call_indicator_claude,
) -> None:
    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "review_storage_unconfigured",
            "Set SIW_DATA_DIR to keep indicator conversations; nothing was read or written",
        )

    def bad_image(request: Request) -> JSONResponse:
        return error_response(
            request,
            422,
            "invalid_indicator_image",
            f"The chart image must be a PNG of at most {MAX_IMAGE_BYTES} bytes",
        )

    def respond(document: dict[str, Any], status_code: int = 200) -> JSONResponse:
        validate_document(document)
        return JSONResponse(status_code=status_code, content=document)

    def model_setting(review: ReviewStore) -> str:
        saved = review.setting(SETTING)
        return saved if saved in MODELS else DEFAULT_MODEL

    def settings_document(review: ReviewStore) -> dict[str, Any]:
        return {
            "kind": "indicator_assistant_settings",
            "schema_version": "0.1",
            "model": model_setting(review),
            "retry_model": RETRY_MODEL,
            "models": list(MODELS),
        }

    @application.get("/v1/indicator-assistant/settings", response_model=None)
    def get_settings(request: Request) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        return respond(settings_document(review))

    @application.put("/v1/indicator-assistant/settings", response_model=None)
    def put_settings(request: Request, body: SettingsRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        review.set_setting(SETTING, body.model)
        return respond(settings_document(review))

    def document(review: ReviewStore, conversation: dict[str, Any]) -> dict[str, Any]:
        state = conversation["state"]
        pending = state.get("pending")
        return {
            "kind": "indicator_conversation",
            "schema_version": "0.1",
            "conversation_id": conversation["conversation_id"],
            "model": conversation["model"],
            "status": state.get("status", "idle"),
            "error": state.get("error"),
            "events": review.events(conversation["conversation_id"]),
            "pending_run": None
            if not pending
            else {"tool_use_id": pending["tool_use_id"], "code": pending["code"]},
            "runs": state.get("runs", 0),
            "max_runs": MAX_RUNS,
            "usage": {
                "input_tokens": state.get("input_tokens", 0),
                "output_tokens": state.get("output_tokens", 0),
                "cache_read_tokens": state.get("cache_read_tokens", 0),
                "cache_write_tokens": state.get("cache_write_tokens", 0),
            },
        }

    def found(
        request: Request, conversation_id: str
    ) -> tuple[ReviewStore, dict[str, Any]] | JSONResponse:
        review = store()
        if review is None:
            return unavailable(request)
        try:
            conversation = review.conversation(conversation_id)
        except ConversationNotFoundError:
            conversation = None
        if conversation is None or not conversation["report_id"].startswith(KEY_PREFIX):
            return error_response(
                request, 404, "conversation_not_found", "No such indicator conversation"
            )
        return review, conversation

    def loop(
        request: Request, review: ReviewStore, conversation: dict[str, Any]
    ) -> IndicatorConversation | JSONResponse:
        key = api_key()
        if key is None:
            return error_response(
                request,
                409,
                "agent_key_missing",
                "Register an enabled Claude (Anthropic) API key on the Keys screen",
            )

        def call(
            model: str,
            system: str,
            tools: list[dict[str, Any]],
            messages: list[Any],
            effort: str,
        ) -> dict[str, Any]:
            return converse(model, system, tools, messages, key, effort=effort)

        return IndicatorConversation(review, conversation, call, now)

    @application.post("/v1/indicator-assistant/conversations", response_model=None)
    def start(request: Request, body: StartRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        if api_key() is None:
            return error_response(
                request,
                409,
                "agent_key_missing",
                "Register an enabled Claude (Anthropic) API key on the Keys screen",
            )
        try:
            image = _image(body.image)
        except ValueError:
            return bad_image(request)
        conversation = review.start_conversation(
            f"{KEY_PREFIX}{uuid.uuid4().hex[:12]}",
            model_setting(review),
            system_prompt(body.language, body.reference),
            {"status": "idle"},
        )
        runner = loop(request, review, conversation)
        if isinstance(runner, JSONResponse):
            return runner
        runner.ask(
            body.question,
            body.context.model_dump(),
            image=image,
            origin=body.origin.model_dump(exclude_none=True) if body.origin else None,
        )
        return respond(document(review, review.conversation(runner.id)), 201)

    @application.get("/v1/indicator-assistant/conversations/{conversation_id}", response_model=None)
    def get_conversation(request: Request, conversation_id: str) -> Response:
        result = found(request, conversation_id)
        if isinstance(result, JSONResponse):
            return result
        review, conversation = result
        return respond(document(review, conversation))

    @application.post(
        "/v1/indicator-assistant/conversations/{conversation_id}/messages", response_model=None
    )
    def ask(request: Request, conversation_id: str, body: AskRequest) -> Response:
        result = found(request, conversation_id)
        if isinstance(result, JSONResponse):
            return result
        review, conversation = result
        try:
            image = _image(body.image)
        except ValueError:
            return bad_image(request)
        runner = loop(request, review, conversation)
        if isinstance(runner, JSONResponse):
            return runner
        try:
            runner.ask(
                body.question, body.context.model_dump(), body.model, body.effort, image=image
            )
        except ConversationError as exc:
            return error_response(request, 409, "conversation_busy", str(exc))
        return respond(document(review, review.conversation(runner.id)))

    @application.post(
        "/v1/indicator-assistant/conversations/{conversation_id}/runs", response_model=None
    )
    def add_run(request: Request, conversation_id: str, body: RunRequest) -> Response:
        result = found(request, conversation_id)
        if isinstance(result, JSONResponse):
            return result
        review, conversation = result
        runner = loop(request, review, conversation)
        if isinstance(runner, JSONResponse):
            return runner
        try:
            runner.add_run(body.tool_use_id, body.ok, body.error, body.summary)
        except ConversationError as exc:
            return error_response(request, 409, "conversation_busy", str(exc))
        return respond(document(review, review.conversation(runner.id)))

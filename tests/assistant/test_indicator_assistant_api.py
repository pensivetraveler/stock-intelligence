"""The Pine indicator assistant: the app runs Claude's scripts (ADR 0042, indicators step 1)."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.assistant.indicator import MAX_IMAGES, MAX_RUNS, call_indicator_claude
from services.review.store import ReviewStore
from tests.review.test_conversation_api import Scripted as BaseScripted
from tests.review.test_conversation_api import answer, tool_use

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
CONTEXT = {"symbol": "AAPL", "timeframe": "5m", "bars": 390, "code": None}
START = {
    "question": "볼린저밴드 하단 두 번 이탈 신호",
    "language": "ko",
    "reference": "Supported: ta.bb, plotshape",
    "context": CONTEXT,
}


class Scripted(BaseScripted):
    """Claude's turns, also recording the effort of each call."""

    def __call__(  # type: ignore[override]
        self,
        model: str,
        system: str,
        tools: list[Any],
        messages: list[Any],
        key: str,
        effort: str = "medium",
    ) -> dict[str, Any]:
        reply = super().__call__(model, system, tools, messages, key)
        self.calls[-1]["effort"] = effort
        return reply


def run(tool_id: str, code: str = "//@version=5\nindicator('x')") -> dict[str, Any]:
    return tool_use((tool_id, "run_pine", {"code": code, "explanation": "first try"}))


def make_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: Scripted, key: str | None
) -> Iterator[TestClient]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    import services.api.main as main

    original = main._claude_key
    main._claude_key = lambda _credentials: key  # type: ignore[assignment]
    app = create_app(
        review_store=ReviewStore(tmp_path / "review.sqlite3", lambda: NOW),
        indicator_converse=script,
        clock=lambda: NOW,
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        main._claude_key = original  # type: ignore[assignment]


@pytest.fixture
def scripted() -> Scripted:
    return Scripted()


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scripted: Scripted
) -> Iterator[TestClient]:
    yield from make_client(tmp_path, monkeypatch, scripted, "test-key-not-real")


def test_run_loop_until_answer(client: TestClient, scripted: Scripted) -> None:
    scripted.turns += [run("t1"), run("t2"), answer("하단 이탈을 표시합니다.")]
    started = client.post("/v1/indicator-assistant/conversations", json=START)
    assert started.status_code == 201, started.text
    document = started.json()
    validate_document(document)
    assert document["status"] == "needs_run"
    assert document["pending_run"]["tool_use_id"] == "t1"
    first = scripted.calls[0]
    assert "Supported: ta.bb" in first["system"]
    assert first["model"] == "claude-sonnet-5-5"
    assert first["effort"] == "medium"
    assert [tool["name"] for tool in first["tools"]] == ["run_pine"]
    sent = json.loads(first["messages"][0]["content"][0]["text"])
    assert sent == {
        "chart": {"symbol": "AAPL", "timeframe": "5m", "bars": 390},
        "editor_code": None,
    }
    conversation_id = document["conversation_id"]
    failed = client.post(
        f"/v1/indicator-assistant/conversations/{conversation_id}/runs",
        json={"tool_use_id": "t1", "ok": False, "error": "3줄 5칸: unknown ta.bbb"},
    ).json()
    assert failed["pending_run"]["tool_use_id"] == "t2"
    result = scripted.calls[1]["messages"][2]["content"][0]
    assert result["is_error"] is True and "unknown ta.bbb" in result["content"]
    done = client.post(
        f"/v1/indicator-assistant/conversations/{conversation_id}/runs",
        json={"tool_use_id": "t2", "ok": True, "summary": "plot 'Basis': last 101.2"},
    ).json()
    validate_document(done)
    assert done["status"] == "idle"
    assert [e["type"] for e in done["events"]] == [
        "question",
        "code",
        "run",
        "code",
        "run",
        "answer",
    ]
    again = client.get(f"/v1/indicator-assistant/conversations/{conversation_id}").json()
    assert again["events"][-1]["text"] == "하단 이탈을 표시합니다."


def test_stops_after_the_last_failed_run(client: TestClient, scripted: Scripted) -> None:
    scripted.turns += [run(f"t{i}") for i in range(MAX_RUNS)]
    document = client.post("/v1/indicator-assistant/conversations", json=START).json()
    conversation_id = document["conversation_id"]
    for i in range(MAX_RUNS):
        document = client.post(
            f"/v1/indicator-assistant/conversations/{conversation_id}/runs",
            json={"tool_use_id": f"t{i}", "ok": False, "error": "1줄 1칸: bad"},
        ).json()
    validate_document(document)
    assert document["status"] == "stopped"
    assert document["runs"] == MAX_RUNS
    assert len(scripted.calls) == MAX_RUNS  # no extra call after the last failure
    # The first retry runs on Sonnet at high effort, after the stored failure.
    scripted.turns.append(answer("고쳤습니다"))
    retried = client.post(
        f"/v1/indicator-assistant/conversations/{conversation_id}/messages",
        json={
            "question": "다시 고쳐줘",
            "context": CONTEXT,
            "model": "claude-sonnet-5-5",
            "effort": "high",
        },
    ).json()
    validate_document(retried)
    assert retried["status"] == "idle"
    assert (scripted.calls[-1]["model"], scripted.calls[-1]["effort"]) == (
        "claude-sonnet-5-5",
        "high",
    )
    assert retried["events"][-2]["effort"] == "high"
    roles = [m["role"] for m in scripted.calls[-1]["messages"]]
    assert roles[-3:] == ["assistant", "user", "user"]
    # The last step is Opus; a later request goes back to the default.
    scripted.turns += [answer("Opus"), answer("기본")]
    client.post(
        f"/v1/indicator-assistant/conversations/{conversation_id}/messages",
        json={"question": "한 번 더", "context": CONTEXT, "model": "claude-opus-5-5"},
    )
    assert (scripted.calls[-1]["model"], scripted.calls[-1]["effort"]) == (
        "claude-opus-5-5",
        "medium",
    )
    client.post(
        f"/v1/indicator-assistant/conversations/{conversation_id}/messages",
        json={"question": "설명", "context": CONTEXT},
    )
    assert (scripted.calls[-1]["model"], scripted.calls[-1]["effort"]) == (
        "claude-sonnet-5-5",
        "medium",
    )
    bad = client.post(
        f"/v1/indicator-assistant/conversations/{conversation_id}/messages",
        json={"question": "x", "context": CONTEXT, "effort": "max"},
    )
    assert bad.status_code == 422


def test_new_request_answers_an_unrun_script(client: TestClient, scripted: Scripted) -> None:
    scripted.turns += [run("t1"), answer("네")]
    document = client.post("/v1/indicator-assistant/conversations", json=START).json()
    conversation_id = document["conversation_id"]
    asked = client.post(
        f"/v1/indicator-assistant/conversations/{conversation_id}/messages",
        json={"question": "그만, 설명만", "context": {**CONTEXT, "code": "plot(close)"}},
    ).json()
    assert asked["status"] == "idle"
    last_user = scripted.calls[-1]["messages"][-1]["content"]
    assert last_user[0]["type"] == "tool_result" and last_user[0]["tool_use_id"] == "t1"
    assert json.loads(last_user[1]["text"])["editor_code"] == "plot(close)"


def test_wrong_run_and_unknown_conversation(client: TestClient, scripted: Scripted) -> None:
    scripted.turns.append(run("t1"))
    document = client.post("/v1/indicator-assistant/conversations", json=START).json()
    wrong = client.post(
        f"/v1/indicator-assistant/conversations/{document['conversation_id']}/runs",
        json={"tool_use_id": "nope", "ok": True},
    )
    assert wrong.status_code == 409
    missing = client.get("/v1/indicator-assistant/conversations/conversation-unknown")
    assert missing.status_code == 404


def test_settings(client: TestClient) -> None:
    document = client.get("/v1/indicator-assistant/settings").json()
    validate_document(document)
    assert document["model"] == "claude-sonnet-5-5"
    assert document["retry_model"] == "claude-opus-5-5"
    changed = client.put("/v1/indicator-assistant/settings", json={"model": "claude-haiku-5-5"})
    assert changed.json()["model"] == "claude-haiku-5-5"


def test_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for client in make_client(tmp_path, monkeypatch, Scripted(), None):
        response = client.post("/v1/indicator-assistant/conversations", json=START)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "agent_key_missing"


def test_request_body() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=answer("ok"))

    call_indicator_claude(
        "claude-sonnet-5-5", "system", [], [], "k", transport=httpx.MockTransport(handler)
    )
    body = json.loads(sent[0].content)
    assert body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default"
    assert body["thinking"]["type"] == "adaptive"
    for banned in ("temperature", "top_p", "top_k", "tool_choice"):
        assert banned not in body
    assert "server-side-fallback" in sent[0].headers["anthropic-beta"]
    call_indicator_claude(
        "claude-haiku-5-5", "s", [], [], "k", transport=httpx.MockTransport(handler)
    )
    assert "fallbacks" not in json.loads(sent[1].content)


PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(32)).decode()
IMAGE = {
    "png_base64": PNG,
    "bars": 120,
    "from_utc": "2026-10-28T13:30:00Z",
    "to_utc": "2026-10-28T20:00:00Z",
}


def user_blocks(call: dict[str, Any], index: int = 0) -> list[dict[str, Any]]:
    return [m for m in call["messages"] if m["role"] == "user"][index]["content"]


def test_starting_points_tell_claude_where_the_code_came_from(
    client: TestClient, scripted: Scripted
) -> None:
    scripted.turns += [answer("옮겼습니다.")]
    code = '//@version=5\nindicator("X")\ntype Foo\nplot(close)'
    started = client.post(
        "/v1/indicator-assistant/conversations",
        json={
            **START,
            "context": {**CONTEXT, "code": code},
            "origin": {"kind": "tradingview", "detail": "line 3, column 1: type is not supported"},
        },
    )
    assert started.status_code == 201, started.text
    document = started.json()
    validate_document(document)
    assert document["events"][0]["origin"] == "tradingview"
    context = json.loads(user_blocks(scripted.calls[0])[0]["text"])
    assert context["editor_code"] == code
    assert context["start_from"] == {
        "kind": "tradingview",
        "detail": "line 3, column 1: type is not supported",
    }
    assert "tradingview" in scripted.calls[0]["system"]


def test_origin_is_only_for_the_first_request(client: TestClient, scripted: Scripted) -> None:
    scripted.turns += [answer("네."), answer("네.")]
    started = client.post(
        "/v1/indicator-assistant/conversations",
        json={**START, "origin": {"kind": "builtin", "detail": "RSI"}},
    ).json()
    later = client.post(
        f"/v1/indicator-assistant/conversations/{started['conversation_id']}/messages",
        json={"question": "기간을 7로", "context": CONTEXT, "origin": {"kind": "builtin"}},
    )
    assert later.status_code == 422


def test_chart_image_is_sent_only_when_attached(client: TestClient, scripted: Scripted) -> None:
    scripted.turns += [answer("봤습니다."), answer("네.")]
    started = client.post("/v1/indicator-assistant/conversations", json={**START, "image": IMAGE})
    assert started.status_code == 201, started.text
    document = started.json()
    validate_document(document)
    assert document["events"][0]["has_image"] is True
    # The bytes stay in the stored history for Claude; the screen's events never carry them.
    assert PNG not in started.text
    blocks = user_blocks(scripted.calls[0])
    images = [block for block in blocks if block["type"] == "image"]
    assert images == [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": PNG},
        }
    ]
    assert any("120 bars" in block.get("text", "") for block in blocks)
    # A later request without an image adds none, and the first is still in the history.
    later = client.post(
        f"/v1/indicator-assistant/conversations/{document['conversation_id']}/messages",
        json={"question": "더 짧게", "context": CONTEXT},
    )
    assert later.status_code == 200, later.text
    assert "has_image" not in later.json()["events"][-2]
    assert [b for b in user_blocks(scripted.calls[1], 1) if b["type"] == "image"] == []
    assert [b for b in user_blocks(scripted.calls[1], 0) if b["type"] == "image"] == images


@pytest.mark.parametrize(
    "png_base64",
    ["not base64!!", "aGVsbG8gd29ybGQ=", "iVBORw0KGgo" + "A" * 6_000_100],
    ids=["not-base64", "not-png", "too-long"],
)
def test_bad_images_are_refused_before_claude(
    client: TestClient, scripted: Scripted, png_base64: str
) -> None:
    response = client.post(
        "/v1/indicator-assistant/conversations",
        json={**START, "image": {**IMAGE, "png_base64": png_base64}},
    )
    assert response.status_code == 422
    assert scripted.calls == []


def test_image_limit_per_conversation(client: TestClient, scripted: Scripted) -> None:
    scripted.turns += [answer("네.") for _ in range(MAX_IMAGES)]
    conversation = client.post(
        "/v1/indicator-assistant/conversations", json={**START, "image": IMAGE}
    ).json()["conversation_id"]
    path = f"/v1/indicator-assistant/conversations/{conversation}/messages"
    for _ in range(MAX_IMAGES - 1):
        ok = client.post(path, json={"question": "또", "context": CONTEXT, "image": IMAGE})
        assert ok.status_code == 200, ok.text
    over = client.post(path, json={"question": "또", "context": CONTEXT, "image": IMAGE})
    assert over.status_code == 409
    assert "5" in over.json()["error"]["message"]
    assert len(scripted.calls) == MAX_IMAGES

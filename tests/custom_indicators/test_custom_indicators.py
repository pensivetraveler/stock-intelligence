"""Python custom indicators: sandboxed, versioned, causal by construction (ADR 0023)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.backtest.indicators import Bar, compute
from services.custom_indicators.store import CustomIndicatorStore

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "indicators" / "parity.json").read_text()
)
BARS = FIXTURE["bars"]

RSI_BAND = """
NAME = "RSI band"
INPUTS = {"length": 14, "high": 70}
OUTPUTS = ["rsi", "over"]
PANE = "separate"

def update(ctx):
    rsi = ctx.indicator("rsi", length=int(ctx.params["length"]))
    if rsi is None:
        return None
    ctx.state["count"] = ctx.state.get("count", 0) + 1
    return {"rsi": rsi, "over": 1 if rsi > ctx.params["high"] else 0}
"""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(indicator_store=CustomIndicatorStore(tmp_path / "i.sqlite3", lambda: NOW))
    with TestClient(app) as test_client:
        yield test_client


def save(client: TestClient, code: str, **extra: Any) -> Any:
    return client.post("/v1/indicators/custom", json={"code": code, **extra})


def run(
    client: TestClient, saved: dict[str, Any], bars: list[dict[str, Any]], **params: Any
) -> Any:
    return client.post(
        f"/v1/indicators/custom/{saved['indicator_id']}/compute",
        json={"version": saved["version"], "params": params, "bars": bars},
    )


def test_saving_checks_the_code_and_versions_it(client: TestClient) -> None:
    first = save(client, RSI_BAND)
    assert first.status_code == 201, first.text
    saved = first.json()
    validate_document(saved)
    assert saved["type"] == f"py:{saved['indicator_id']}@1"
    assert saved["inputs"] == [{"key": "length", "default": 14}, {"key": "high", "default": 70}]
    assert (saved["outputs"], saved["pane"]) == (["rsi", "over"], "separate")
    same = save(client, RSI_BAND, indicator_id=saved["indicator_id"]).json()
    assert same["version"] == 1
    changed = save(
        client, RSI_BAND.replace('"RSI band"', '"RSI band 2"'), indicator_id=saved["indicator_id"]
    ).json()
    assert (changed["version"], changed["versions"]) == (2, [1, 2])
    listed = client.get("/v1/indicators/custom").json()
    validate_document(listed)
    assert [item["version"] for item in listed["indicators"]] == [1, 2]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("NAME = 'x'\nOUTPUTS = ['v']\n", "must define update"),
        ("NAME = 'x'\nOUTPUTS = []\ndef update(ctx):\n    return None\n", "OUTPUTS"),
        ("NAME = 'x'\nOUTPUTS = ['v']\nPANE = 'top'\ndef update(ctx):\n    return None\n", "PANE"),
        ("def update(ctx) return\n", "does not compile"),
        (
            "NAME = 'x'\nOUTPUTS = ['v']\ndef update(ctx):\n    import socket\n",
            "may not import socket",
        ),
        (
            "NAME = 'x'\nOUTPUTS = ['v']\nINPUTS = {'Len': 3}\ndef update(ctx):\n    return None\n",
            "lowercase",
        ),
    ],
)
def test_bad_indicators_are_refused(client: TestClient, code: str, message: str) -> None:
    response = save(client, code)
    assert response.status_code == 422
    assert message in response.json()["error"]["message"]


def test_values_match_the_engine_and_never_change_with_later_bars(client: TestClient) -> None:
    saved = save(client, RSI_BAND).json()
    full = run(client, saved, BARS)
    assert full.status_code == 200, full.text
    result = full.json()
    validate_document(result)
    expected = compute("rsi", [Bar(**bar) for bar in BARS], {"length": 14})["value"]
    assert result["outputs"]["rsi"] == pytest.approx(expected, abs=1e-12, nan_ok=True)
    assert result["times"] == [bar["time"] for bar in BARS]
    early = run(client, saved, BARS[:90]).json()
    for name in ("rsi", "over"):
        assert result["outputs"][name][:90] == early["outputs"][name]
    other = run(client, saved, BARS, length=5, high=60).json()
    assert other["outputs"]["rsi"] != result["outputs"]["rsi"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("return {'v': ctx.history[ctx.index + 1].close}", "only bars up to the current one"),
        ("return {'v': float('nan')}", "not a finite number"),
        ("return {'v': 1, 'w': 2}", "undeclared outputs"),
        ("return {'v': 'high'}", "must be a number"),
        ("while True:\n        pass", "did not answer"),
    ],
)
def test_peeking_ahead_and_bad_values_fail_the_computation(
    client: TestClient, body: str, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.custom_indicators.runner as runner_module

    monkeypatch.setattr(runner_module, "RUN_TIMEOUT", 2.0)
    code = (
        "NAME = 'x'\nOUTPUTS = ['v']\ndef update(ctx):\n"
        f"    if ctx.index < 3:\n        return None\n    {body}\n"
    )
    saved = save(client, code)
    assert saved.status_code == 201, saved.text
    response = run(client, saved.json(), BARS[:20])
    assert response.status_code == 422
    assert message in response.json()["error"]["message"]


def test_settings_and_bars_are_checked(client: TestClient) -> None:
    saved = save(client, RSI_BAND).json()
    assert "unknown settings" in run(client, saved, BARS, size=3).json()["error"]["message"]
    backwards = [BARS[1], BARS[0]]
    assert "time order" in run(client, saved, backwards).json()["error"]["message"]
    missing = client.post(
        "/v1/indicators/custom/nope/compute", json={"version": 1, "bars": BARS[:2]}
    )
    assert missing.status_code == 404

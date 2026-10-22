"""Allowed browser origins, including the Electron shell's (ADR 0028)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api.main import create_app


@pytest.mark.parametrize("configured", [None, "http://localhost:5173"])
def test_electron_shell_origin_is_allowed_even_with_configured_origins(
    configured: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if configured is None:
        monkeypatch.delenv("SIW_CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("SIW_CORS_ORIGINS", configured)
    with TestClient(create_app()) as client:
        shell = client.get("/health", headers={"Origin": "siw://app"})
        web = client.get("/health", headers={"Origin": "http://localhost:5173"})
        other = client.get("/health", headers={"Origin": "https://example.com"})
    assert shell.headers.get("access-control-allow-origin") == "siw://app"
    assert web.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in other.headers

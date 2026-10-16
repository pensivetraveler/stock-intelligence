from __future__ import annotations

import json
import re
from pathlib import Path

from services.api.main import APP_VERSION

ROOT = Path(__file__).resolve().parents[1]


def _toml_version(path: Path) -> str:
    match = re.search(r'^version = "([^"]+)"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"no version in {path}"
    return match.group(1)


def _json_version(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["version"])


def test_application_version_is_identical_across_ecosystems() -> None:
    """One product version; PEP 440 spells the semver pre-release `-dev` as `.dev0`."""
    versions = {
        "services/api/main.py": APP_VERSION,
        "package.json": _json_version(ROOT / "package.json"),
        "apps/desktop/package.json": _json_version(ROOT / "apps/desktop/package.json"),
        "tauri.conf.json": _json_version(ROOT / "apps/desktop/src-tauri/tauri.conf.json"),
        "Cargo.toml": _toml_version(ROOT / "apps/desktop/src-tauri/Cargo.toml"),
        "pyproject.toml": _toml_version(ROOT / "pyproject.toml").replace(".dev0", "-dev"),
    }

    assert set(versions.values()) == {APP_VERSION}, versions

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_paper_only", "scripts/check_paper_only.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_paper_trading_package_passes() -> None:
    assert _checker().problems(Path("services/paper_trading")) == []


@pytest.mark.parametrize(
    "line",
    [
        'BASE = "https://api.kiwoom.com"',
        'BASE = "https://openapi.koreainvestment.com:9443"',
        'TR_ID = "TTTT1002U"',
        "from services.market_data.kiwoom_account import REAL_BASE_URL",
    ],
)
def test_live_names_fail(tmp_path: Path, line: str) -> None:
    (tmp_path / "adapter.py").write_text(line + "\n", encoding="utf-8")
    assert _checker().problems(tmp_path)


def test_paper_hosts_pass(tmp_path: Path) -> None:
    (tmp_path / "adapter.py").write_text(
        'A = "https://mockapi.kiwoom.com"\nB = "https://openapivts.koreainvestment.com:29443"\n'
        'C = "VTTT1002U"\n',
        encoding="utf-8",
    )
    assert _checker().problems(tmp_path) == []

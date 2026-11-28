"""systemd user units for the market-hours collectors (ADR 0059 step 1). No systemctl runs."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from services.collector import systemd
from services.collector.systemd import COLLECTORS, Collector


def _repo(tmp_path: Path, env: str | None = "SIW_DATA_DIR=data\n") -> Path:
    repo = tmp_path / "stock-intelligence"
    (repo / "services" / "collector").mkdir(parents=True)
    if env is not None:
        (repo / ".env").write_text(env, encoding="utf-8")
    return repo


class FakeSystemctl:
    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, arguments: Sequence[str]) -> tuple[bool, str]:
        self.calls.append(list(arguments))
        if self.fail and self.fail in arguments:
            return False, "Failed to connect to bus"
        return True, ""


def test_data_dir_follows_the_api_env_relative_to_the_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "# comment\nSIW_DATA_DIR=old\nexport SIW_DATA_DIR='data'\n")
    assert systemd.resolve_data_dir(repo, None) == (repo / "data").resolve()
    assert systemd.resolve_data_dir(repo, Path("/srv/siw")) == Path("/srv/siw")
    assert systemd.resolve_data_dir(_repo(tmp_path / "x", None), None) is None


def test_unit_runs_the_collector_from_the_repository_without_keys(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    collector = COLLECTORS[0]
    text = systemd.unit_text(
        collector, Path("/home/u/.local/bin/uv"), repo, repo / "data", tmp_path / "logs"
    )
    assert f"WorkingDirectory={repo}" in text
    assert (
        'ExecStart="/home/u/.local/bin/uv" "run" "--env-file" ".env" "python" "-u" "-m" '
        f'"services.collector" "watch-condition" "--data-dir" "{repo / "data"}"'
    ) in text
    # SIGTERM goes to uv alone, which forwards it once (2026-11-28: python saw it twice and hung).
    assert "KillMode=mixed\nRestart=always" in text
    assert f"StandardOutput=append:{tmp_path / 'logs' / 'watch-condition.log'}" in text
    assert "WantedBy=default.target" in text
    lowered = text.lower()
    for word in ("secret", "token", "password", "app_key", "api_key"):
        assert word not in lowered


def test_unit_escapes_specifiers_and_refuses_line_breaks(tmp_path: Path) -> None:
    repo = tmp_path / "a%b$c"
    text = systemd.unit_text(COLLECTORS[1], Path("/u/uv"), repo, repo / "data", tmp_path)
    assert f"WorkingDirectory={tmp_path}/a%%b$c" in text
    assert '"--data-dir" "' + f"{tmp_path}/a%%b$$c/data" + '"' in text
    with pytest.raises(ValueError):
        systemd.unit_text(COLLECTORS[1], Path("/u/uv"), Path("/a\nExecStart=/bin/sh"), repo, repo)


def test_install_writes_both_units_and_starts_them(tmp_path: Path) -> None:
    repo, units, logs = _repo(tmp_path), tmp_path / "units", tmp_path / "logs"
    fake = FakeSystemctl()
    problems = systemd.install(
        COLLECTORS, Path("/u/uv"), repo, repo / "data", units=units, logs=logs, systemctl=fake
    )
    assert problems == []
    assert sorted(path.name for path in units.iterdir()) == sorted(
        item.unit_name for item in COLLECTORS
    )
    assert logs.is_dir()
    assert fake.calls[0] == ["daemon-reload"]
    assert ["enable", COLLECTORS[0].unit_name] in fake.calls
    assert ["restart", COLLECTORS[1].unit_name] in fake.calls


def test_install_without_start_only_enables(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    fake = FakeSystemctl()
    systemd.install(
        COLLECTORS[:1],
        Path("/u/uv"),
        repo,
        repo / "data",
        units=tmp_path / "u",
        logs=tmp_path / "l",
        systemctl=fake,
        start=False,
    )
    assert not any(call[0] == "restart" for call in fake.calls)


def test_install_reports_the_first_failing_step(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    fake = FakeSystemctl(fail="daemon-reload")
    problems = systemd.install(
        COLLECTORS,
        Path("/u/uv"),
        repo,
        repo / "data",
        units=tmp_path / "u",
        logs=tmp_path / "l",
        systemctl=fake,
    )
    assert problems and "daemon-reload" in problems[0]
    assert fake.calls == [["daemon-reload"]]


def test_uninstall_disables_and_removes_the_unit(tmp_path: Path) -> None:
    units = tmp_path / "units"
    units.mkdir()
    only: Collector = COLLECTORS[1]
    (units / only.unit_name).write_text("x", encoding="utf-8")
    fake = FakeSystemctl()
    assert systemd.uninstall([only], units=units, systemctl=fake) == []
    assert ["disable", "--now", only.unit_name] in fake.calls
    assert not (units / only.unit_name).exists()


def test_print_shows_units_on_any_os_and_install_needs_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert systemd.main(["print", "--repo", str(repo), "--uv", "/u/uv"]) == 0
    printed = capsys.readouterr().out
    assert "watch-condition" in printed and "watch-halts" in printed
    monkeypatch.setattr(systemd.platform, "system", lambda: "Darwin")
    assert systemd.main(["install", "--repo", str(repo), "--uv", "/u/uv"]) == 2
    assert not (tmp_path / "config").exists()

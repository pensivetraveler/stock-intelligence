"""The market-hours collectors as Linux systemd user services (ADR 0059 step 1).

`python -m services.collector.systemd install|stop|start|uninstall|status|print`

Same rules as the Agent runner's units (ADR 0045, `apps/desktop/src-tauri/src/systemd.rs`):

- a user service, started at login and restarted after a crash;
- lingering is not enabled, so a collector only runs while the session that unlocks the
  keyring exists;
- the unit names the repository, uv and the data directory only; no keys are written.

Only one machine may run a collector: the same Kiwoom account (and Alpaca account) takes a
single realtime connection, and a second one closes the first (ADR 0051). Stop the Mac
LaunchAgent before starting these.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PREFIX = "com.pensivetraveler.siw.collector"


@dataclass(frozen=True)
class Collector:
    key: str
    command: str
    description: str

    @property
    def unit_name(self) -> str:
        return f"{PREFIX}.{self.command}.service"

    @property
    def log_name(self) -> str:
        return f"{self.command}.log"


COLLECTORS = (
    Collector(
        "condition", "watch-condition", "SIW collector: Kiwoom realtime condition (ADR 0051)"
    ),
    Collector("halts", "watch-halts", "SIW collector: Alpaca SIP trading statuses (ADR 0057)"),
)

Systemctl = Callable[[Sequence[str]], "tuple[bool, str]"]


def _xdg_dir(variable: str, default: str, environ: dict[str, str] | None = None) -> Path:
    value = (environ if environ is not None else os.environ).get(variable)
    if value and Path(value).is_absolute():
        return Path(value)
    return Path.home() / default


def unit_dir(environ: dict[str, str] | None = None) -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", ".config", environ) / "systemd" / "user"


def log_dir(environ: dict[str, str] | None = None) -> Path:
    return _xdg_dir("XDG_STATE_HOME", ".local/state", environ) / "siw-collector"


def find_uv() -> Path | None:
    """uv in the usual places: a systemd user service does not get the shell's PATH."""
    candidates = [Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv"]
    if found := shutil.which("uv"):
        candidates.insert(0, Path(found))
    return next((path.absolute() for path in candidates if path.is_file()), None)


def _env_value(env_file: Path, name: str) -> str | None:
    """A plain `NAME=value` line from `.env`; the last one wins, like the API's loader."""
    value = None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        if key.strip() == name:
            raw = raw.strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                raw = raw[1:-1]
            value = raw
    return value


def resolve_data_dir(repo: Path, explicit: Path | None) -> Path | None:
    """The data directory the Agent's API uses: `--data-dir`, else `SIW_DATA_DIR` in `.env`.

    Keys are registered through that API, so the collectors must read the same registry.
    A relative value is taken from the repository, where the API runs.
    """
    value: str | None = str(explicit) if explicit is not None else None
    env_file = repo / ".env"
    if value is None and env_file.is_file():
        value = _env_value(env_file, "SIW_DATA_DIR")
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (repo / path).resolve()


def _escape_line(text: str) -> str:
    return text.replace("%", "%%")


def _quote_exec_word(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def _quote_environment(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def exec_arguments(collector: Collector, uv: Path, repo: Path, data_dir: Path) -> list[str]:
    arguments = [str(uv), "run"]
    if (repo / ".env").is_file():
        arguments += ["--env-file", ".env"]
    arguments += [
        "python",
        "-u",
        "-m",
        "services.collector",
        collector.command,
        "--data-dir",
        str(data_dir),
    ]
    return arguments


def unit_text(collector: Collector, uv: Path, repo: Path, data_dir: Path, logs: Path) -> str:
    # `KillMode=mixed`: the stop SIGTERM goes to uv alone, which forwards it to python once. The
    # default (`control-group`) signalled both, python saw it twice and once hung until SIGKILL
    # (2026-11-28 mini PC). Whatever outlives the stop timeout is still killed.
    for path in (uv, repo, data_dir, logs):
        if "\n" in str(path) or "\r" in str(path):
            # A line break would end the value and start another unit setting.
            raise ValueError("a path contains a line break")
    program = " ".join(
        _quote_exec_word(argument) for argument in exec_arguments(collector, uv, repo, data_dir)
    )
    cache = _quote_environment(f"UV_CACHE_DIR={repo / '.uv-cache'}")
    log = _escape_line(str(logs / collector.log_name))
    return f"""[Unit]
Description={collector.description}

[Service]
Type=simple
WorkingDirectory={_escape_line(str(repo))}
Environment={cache}
ExecStart={program}
KillMode=mixed
Restart=always
RestartSec=60
StandardOutput=append:{log}
StandardError=append:{log}

[Install]
WantedBy=default.target
"""


def _systemctl(arguments: Sequence[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            ["systemctl", "--user", *arguments], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    return completed.returncode == 0, completed.stdout + completed.stderr


def _selected(only: str | None) -> list[Collector]:
    return [item for item in COLLECTORS if only in (None, item.key)]


def install(
    collectors: Sequence[Collector],
    uv: Path,
    repo: Path,
    data_dir: Path,
    *,
    units: Path,
    logs: Path,
    systemctl: Systemctl = _systemctl,
    start: bool = True,
) -> list[str]:
    """Write the units, enable them for the next login, and start them unless told not to."""
    units.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    for collector in collectors:
        (units / collector.unit_name).write_text(
            unit_text(collector, uv, repo, data_dir, logs), encoding="utf-8"
        )
    problems: list[str] = []
    steps: list[list[str]] = [["daemon-reload"]]
    for collector in collectors:
        steps.append(["enable", collector.unit_name])
        if start:
            steps.append(["restart", collector.unit_name])
    for step in steps:
        ok, output = systemctl(step)
        if not ok:
            problems.append(f"systemctl --user {' '.join(step)}: {output.strip()}")
            break
    return problems


def uninstall(
    collectors: Sequence[Collector], *, units: Path, systemctl: Systemctl = _systemctl
) -> list[str]:
    problems: list[str] = []
    for collector in collectors:
        ok, output = systemctl(["disable", "--now", collector.unit_name])
        if not ok and (units / collector.unit_name).is_file():
            problems.append(f"{collector.unit_name}: {output.strip()}")
        (units / collector.unit_name).unlink(missing_ok=True)
    systemctl(["daemon-reload"])
    return problems


def status(
    collectors: Sequence[Collector], *, units: Path, systemctl: Systemctl = _systemctl
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for collector in collectors:
        _, active = systemctl(["is-active", collector.unit_name])
        _, enabled = systemctl(["is-enabled", collector.unit_name])
        rows.append(
            {
                "unit": collector.unit_name,
                "installed": (units / collector.unit_name).is_file(),
                "active": active.strip(),
                "enabled": enabled.strip(),
            }
        )
    return rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m services.collector.systemd")
    parser.add_argument(
        "action", choices=("install", "start", "stop", "uninstall", "status", "print")
    )
    parser.add_argument("--only", choices=[item.key for item in COLLECTORS])
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="default: current dir")
    parser.add_argument("--data-dir", type=Path, help="default: SIW_DATA_DIR in <repo>/.env")
    parser.add_argument("--uv", type=Path, help="default: uv found on PATH or in ~/.local/bin")
    parser.add_argument(
        "--no-start", action="store_true", help="install: enable for the next login only"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    collectors = _selected(args.only)
    units, logs = unit_dir(), log_dir()
    if args.action == "status":
        for row in status(collectors, units=units):
            print(row)
        return 0
    if args.action in ("start", "stop"):
        failed = False
        for collector in collectors:
            ok, output = _systemctl([args.action, collector.unit_name])
            if not ok:
                print(f"{collector.unit_name}: {output.strip()}", file=sys.stderr)
                failed = True
        return 1 if failed else 0
    if args.action == "uninstall":
        problems = uninstall(collectors, units=units)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0

    repo = args.repo.expanduser().resolve()
    if not (repo / "services" / "collector").is_dir():
        print(f"not the repository: {repo}", file=sys.stderr)
        return 2
    data_dir = resolve_data_dir(repo, args.data_dir)
    if data_dir is None:
        print("no data dir: pass --data-dir or set SIW_DATA_DIR in .env", file=sys.stderr)
        return 2
    uv = args.uv.expanduser().absolute() if args.uv else find_uv()
    if uv is None:
        print("uv not found: pass --uv", file=sys.stderr)
        return 2
    if args.action == "print":
        for collector in collectors:
            print(f"# {units / collector.unit_name}")
            print(unit_text(collector, uv, repo, data_dir, logs))
        return 0
    if platform.system() != "Linux":
        print("install is for the Linux mini PC (ADR 0059); use print to inspect", file=sys.stderr)
        return 2
    problems = install(
        collectors, uv, repo, data_dir, units=units, logs=logs, start=not args.no_start
    )
    for problem in problems:
        print(problem, file=sys.stderr)
    for row in status(collectors, units=units):
        print(row)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

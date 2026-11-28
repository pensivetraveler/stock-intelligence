import json
import subprocess
import sys
from pathlib import Path

from services.collector.cli import main


def test_doctor_is_offline_and_successful(capsys: object) -> None:
    assert main(["doctor"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert json.loads(captured.out) == {
        "external_calls": False,
        "service": "collector",
        "status": "ok",
    }


def test_missing_config_is_usage_error(tmp_path: Path, capsys: object) -> None:
    assert main(["doctor", "--config", str(tmp_path / "missing.json")]) == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert json.loads(captured.out)["error"]["code"] == "invalid_config"


def test_run_stops_cleanly_on_sigterm() -> None:
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "services.collector", "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait for "running" (handlers installed) rather than a fixed sleep: under load the
        # signal used to land before the handlers and kill the process with -15.
        assert process.stdout is not None
        first = process.stdout.readline()
        process.terminate()
        stdout, stderr = process.communicate(timeout=3)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0
    assert stderr == ""
    assert [json.loads(line)["status"] for line in [first, *stdout.splitlines()]] == [
        "running",
        "stopped",
    ]

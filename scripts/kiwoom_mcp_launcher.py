"""Start Kiwoom's `kiwoom-exec` MCP server with a key registered on the Keys screen.

    uv run --directory <this repo> python scripts/kiwoom_mcp_launcher.py \\
        --data-dir <SIW_DATA_DIR> --server-dir <Kiwoom-REST-API>/mcp_exec \\
        [--environment demo|real] [--label 해외주식]

The secret values are read from the OS keychain only here, at start-up, and are handed to
the MCP server process through its environment. They are never written to the MCP client
config, to disk or to output. The key is the first enabled Kiwoom key in the Keys screen
order, optionally only among real or demo accounts (--environment) or one label (--label).
KIWOOM_MODE is set from that key's account type as registered in this app, so the key and
the Kiwoom server (real or demo) always match.

stdout belongs to the MCP protocol, so this script prints nothing there; errors go to
stderr and never contain secret values.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.credentials.store import CredentialStore, KeyringSecretBackend  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kiwoom_mcp_launcher")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--server-dir", required=True, type=Path)
    parser.add_argument("--environment", choices=("demo", "real"), default=None)
    parser.add_argument("--label", default=None)
    args = parser.parse_args(argv)

    store = CredentialStore(args.data_dir, KeyringSecretBackend())
    records = [
        record
        for record in store.list("kiwoom")
        if record.enabled
        and (args.environment is None or record.environment == args.environment)
        and (args.label is None or record.label == args.label)
    ]
    if not records:
        kind = f"{args.environment} " if args.environment else ""
        wanted = f" labelled {args.label!r}" if args.label else ""
        print(
            f"kiwoom_mcp_launcher: no enabled {kind}Kiwoom key{wanted} is "
            "registered on the Keys screen",
            file=sys.stderr,
        )
        return 2
    record = records[0]
    if record.environment not in ("real", "demo"):
        print("kiwoom_mcp_launcher: the key has no account type; set it", file=sys.stderr)
        return 2
    secrets = store.secrets(record)

    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)  # this repo's venv; the server runs in its own
    env["APP_KEY"] = secrets["app_key"]
    env["APP_SECRET"] = secrets["secret_key"]
    env["KIWOOM_MODE"] = record.environment
    os.execve(
        uv,
        [uv, "run", "--frozen", "--directory", str(args.server_dir), "kiwoom-exec-mcp"],
        env,
    )
    return 0  # not reached


if __name__ == "__main__":
    sys.exit(main())

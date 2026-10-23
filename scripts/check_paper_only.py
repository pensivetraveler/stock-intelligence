"""Static check that paper-order code names no live broker domain (ADR 0037 §4).

Fails when `services/paper_trading` contains a live host, a live KIS order TR ID, or an
import of the read-only modules' live base URLs. Hosts are matched whole, so
`mockapi.kiwoom.com` does not count as `api.kiwoom.com`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LIVE_HOSTS = re.compile(
    r"(?<![A-Za-z0-9.-])(api\.kiwoom\.com|openapi\.koreainvestment\.com)(?![A-Za-z0-9-])"
)
LIVE_KIS_TR_ID = re.compile(r"""["'](T[A-Z]{3}[0-9]{4}[A-Z])["']""")
LIVE_IMPORTS = re.compile(
    r"from\s+services\.market_data\.(kis|kiwoom|kiwoom_account)\s+import|REAL_BASE_URL"
)


def problems(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".py", ".sql", ".json"} or not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, label in (
                (LIVE_HOSTS, "live broker host"),
                (LIVE_KIS_TR_ID, "live KIS TR ID"),
                (LIVE_IMPORTS, "live base URL import"),
            ):
                if pattern.search(line):
                    found.append(f"{path}:{number}: {label}: {line.strip()}")
    return found


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("services/paper_trading")
    found = problems(root)
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

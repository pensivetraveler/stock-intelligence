"""Splits the dilution analyzer read from SEC filings, in the shared data space (ADR 0046).

The analyzer owns `splits.sqlite`; this module only reads it. The values come from rules
over filings and were not compared with another source, so they are evidence shown next
to the provider's splits. Nothing here changes a price, and a difference does not say
which side is wrong.

`read_filing_splits` is the only reader of that file, so it can become an HTTP call when
the analyzer moves off this machine.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.parse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

SPLITS_VERSION = 1  # PRAGMA user_version this reader understands
LAYOUT_VERSION = 1
DISAGREEMENTS_VERSION = 1
SAME_EVENT_DAYS = 3  # a filing's effective date to the ex-date, across a weekend
READ_TIMEOUT_SECONDS = 2.0

CONTRACT: dict[str, Any] = {
    "layout_version": LAYOUT_VERSION,
    "decided_in": "stock-intelligence ADR 0046",
    "datasets": {
        "dilution/": {"writer": "stock-dilution-analyzer", "readers": {"siw": ["splits.sqlite"]}},
        "siw/split-disagreements.json": {"writer": "siw", "format": DISAGREEMENTS_VERSION},
        "sec/": {
            "writers": ["stock-dilution-analyzer", "siw"],
            "files": "<host>/<sha256(url)[:16]>-<last path segment>, decoded 200 bodies",
            "immutable": "/Archives/ paths: never replaced",
        },
        "sec.lock": {"protocol": "flock, CLOCK_MONOTONIC stamp", "interval_seconds": 0.2},
    },
}


class FilingSplitsUnavailable(RuntimeError):
    """The file is missing, unreadable, or in a version this reader does not know."""


@dataclass(frozen=True)
class FilingSplit:
    effective: date
    numerator: int  # new shares
    denominator: int  # old shares
    status: str  # confirmed, scheduled or conflict

    @property
    def ratio(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)


@dataclass(frozen=True)
class FilingSplits:
    status: str  # not_checked, none_found or found
    filings_from: date | None = None  # before this nothing was read: not checked
    as_of: date | None = None
    rows: tuple[FilingSplit, ...] = ()


@dataclass(frozen=True)
class ProviderSplit:
    on: date
    ratio: Fraction  # new shares per old share


@dataclass(frozen=True)
class SplitDifference:
    kind: str  # ratio_differs, filing_only, provider_only or filing_unconfirmed
    filing: FilingSplit | None
    provider: ProviderSplit | None


def shared_data_dir() -> Path | None:
    configured = os.getenv("STOCK_SHARED_DATA", "").strip()
    return Path(configured).expanduser() if configured else None


def splits_path(root: Path) -> Path:
    return root / "dilution" / "splits.sqlite"


def read_filing_splits(path: Path, ticker: str) -> FilingSplits:
    """What the analyzer knows of `ticker`. A short read: a long one blocks its writes."""
    if not path.is_file():
        raise FilingSplitsUnavailable(f"{path.name} is not in the shared data space")
    uri = f"file:{urllib.parse.quote(str(path))}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=READ_TIMEOUT_SECONDS)
    except sqlite3.Error as exc:
        raise FilingSplitsUnavailable(f"could not open {path.name}: {exc}") from exc
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SPLITS_VERSION:
            raise FilingSplitsUnavailable(f"{path.name} has unknown version {version}")
        known = connection.execute(
            "SELECT cik FROM tickers WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
        if known is None:
            return FilingSplits("not_checked")
        issuer = connection.execute(
            "SELECT filings_from, as_of FROM issuers WHERE cik = ?", known
        ).fetchone()
        if issuer is None:
            return FilingSplits("not_checked")
        rows = connection.execute(
            "SELECT effective, numerator, denominator, status FROM splits "
            "WHERE cik = ? ORDER BY effective, denominator",
            known,
        ).fetchall()
    except sqlite3.Error as exc:
        raise FilingSplitsUnavailable(f"could not read {path.name}: {exc}") from exc
    finally:
        connection.close()
    return FilingSplits(
        "found" if rows else "none_found",
        date.fromisoformat(issuer[0]),
        date.fromisoformat(issuer[1][:10]),
        tuple(FilingSplit(date.fromisoformat(r[0]), r[1], r[2], r[3]) for r in rows),
    )


def checked_tickers(path: Path) -> list[str]:
    """Every ticker with a read issuer, for the export."""
    uri = f"file:{urllib.parse.quote(str(path))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=READ_TIMEOUT_SECONDS)
    try:
        return [
            row[0]
            for row in connection.execute(
                "SELECT t.ticker FROM tickers t JOIN issuers i USING (cik) ORDER BY t.ticker"
            )
        ]
    finally:
        connection.close()


def compare_splits(
    filing: FilingSplits, provider: Iterable[ProviderSplit]
) -> list[SplitDifference]:
    """Where confirmed filing readings and the provider's splits differ.

    Only the period the analyzer read is compared. An action outside it says nothing:
    the filings of that time were not read.
    """
    if filing.filings_from is None or filing.as_of is None:
        return []
    start, end = filing.filings_from, filing.as_of
    actions = [action for action in provider if start <= action.on <= end]
    confirmed = [row for row in filing.rows if row.status == "confirmed"]
    others = [row for row in filing.rows if row.status != "confirmed"]

    def near(row: FilingSplit, action: ProviderSplit) -> bool:
        return abs((row.effective - action.on).days) <= SAME_EVENT_DAYS

    differences: list[SplitDifference] = []
    used: set[int] = set()
    for row in confirmed:
        candidates = [i for i, a in enumerate(actions) if i not in used and near(row, a)]
        same = next((i for i in candidates if actions[i].ratio == row.ratio), None)
        if same is not None:
            used.add(same)
        elif candidates:
            used.add(candidates[0])
            differences.append(SplitDifference("ratio_differs", row, actions[candidates[0]]))
        else:
            differences.append(SplitDifference("filing_only", row, None))
    for index, action in enumerate(actions):
        if index in used:
            continue
        unconfirmed = next(
            (row for row in others if near(row, action) and row.ratio == action.ratio), None
        )
        kind = "filing_unconfirmed" if unconfirmed is not None else "provider_only"
        differences.append(SplitDifference(kind, unconfirmed, action))
    return sorted(differences, key=_difference_date)


def _difference_date(difference: SplitDifference) -> date:
    if difference.provider is not None:
        return difference.provider.on
    assert difference.filing is not None
    return difference.filing.effective


def filing_split_document(row: FilingSplit) -> dict[str, Any]:
    return {
        "effective": row.effective.isoformat(),
        "numerator": row.numerator,
        "denominator": row.denominator,
        "status": row.status,
    }


def difference_document(difference: SplitDifference) -> dict[str, Any]:
    action = difference.provider
    return {
        "kind": difference.kind,
        "filing": None if difference.filing is None else filing_split_document(difference.filing),
        "provider": None
        if action is None
        else {
            "on": action.on.isoformat(),
            "numerator": action.ratio.numerator,
            "denominator": action.ratio.denominator,
        },
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def export_disagreements(
    root: Path,
    provider_splits: Callable[[str, date, date], Sequence[ProviderSplit]],
    source_id: str,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Path:
    """Write `siw/split-disagreements.json`, and `CONTRACT.json` when it is out of date."""
    path = splits_path(root)
    if not path.is_file():
        raise FilingSplitsUnavailable(f"{path.name} is not in the shared data space")
    items: list[dict[str, Any]] = []
    tickers = checked_tickers(path)
    for ticker in tickers:
        filing = read_filing_splits(path, ticker)
        if filing.filings_from is None or filing.as_of is None:
            continue
        actions = provider_splits(ticker, filing.filings_from, filing.as_of)
        for difference in compare_splits(filing, actions):
            items.append({"ticker": ticker, **difference_document(difference)})
    _write_json(root / "CONTRACT.json", CONTRACT)
    output = root / "siw" / "split-disagreements.json"
    _write_json(
        output,
        {
            "format": DISAGREEMENTS_VERSION,
            "generated_at_utc": now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider": source_id,
            "same_event_days": SAME_EVENT_DAYS,
            "note": "A difference does not say which side is wrong.",
            "tickers_compared": tickers,
            "differences": items,
        },
    )
    return output


def main(arguments: Sequence[str] | None = None) -> int:
    if list(arguments if arguments is not None else sys.argv[1:]) != ["export"]:
        print("usage: python -m services.market_data.filing_splits export", file=sys.stderr)
        return 2
    root = shared_data_dir()
    if root is None:
        print("set STOCK_SHARED_DATA", file=sys.stderr)
        return 2
    from services.api.main import _LazyBoats, _LazyCredentials

    adapter = _LazyBoats(_LazyCredentials(None).get).get()
    if adapter is None:
        print("no Alpaca key is registered", file=sys.stderr)
        return 1
    print(export_disagreements(root, adapter.split_actions, "alpaca-corporate-actions"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

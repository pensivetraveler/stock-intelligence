"""Public source records in `SIW_DATA_DIR/public_sources.sqlite3` (ADR 0024).

A record is kept per revision: when a source changes a record (a FINRA correction, a
restated XBRL fact), the new version is stored next to the old one. Each revision keeps
the source's own publication time and the time this system first observed it, which is
never changed afterwards. A retrospective download therefore never pretends the record
was seen at its publication time.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    source_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    checksum TEXT NOT NULL,
    published_at_utc TEXT,
    first_observed_utc TEXT NOT NULL,
    last_retrieved_utc TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (source_id, record_id, checksum)
);
CREATE INDEX IF NOT EXISTS records_by_key ON records (source_id, record_id);
CREATE TABLE IF NOT EXISTS fetches (
    source_id TEXT NOT NULL,
    fetch_key TEXT NOT NULL,
    last_retrieved_utc TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY (source_id, fetch_key)
);
"""


def utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def checksum(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    record_id: str
    checksum: str
    published_at_utc: str | None
    #: The first time this system stored any revision of the record.
    first_observed_utc: str
    last_retrieved_utc: str
    parser_version: str
    document: dict[str, Any]
    revisions: int

    def contract(self) -> dict[str, Any]:
        """The project's `source_record` document. `retrieved_at_utc` is the first
        observation; no `available_at_utc` is claimed, since it was not observed."""
        document: dict[str, Any] = {
            "kind": "source_record",
            "schema_version": "0.1",
            "source_id": self.source_id,
            "record_ref": self.record_id,
            "payload_sha256": self.checksum,
            "retrieved_at_utc": self.first_observed_utc,
            "parser_version": self.parser_version,
        }
        if self.published_at_utc is not None:
            document["published_at_utc"] = self.published_at_utc
        return document

    def observed_after_publication(self) -> bool:
        return self.published_at_utc is not None and parse_utc(self.first_observed_utc) > parse_utc(
            self.published_at_utc
        )


class PublicSourceCache:
    def __init__(self, path: Path, clock: Callable[[], datetime]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._clock = clock

    def now(self) -> datetime:
        return self._clock()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Many writes in one transaction (a submissions page holds about 1,000 filings)."""
        with self._lock:
            self._db.execute("BEGIN")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    # --- fetch bookkeeping -------------------------------------------------------------

    def last_fetch(self, source_id: str, fetch_key: str) -> datetime | None:
        with self._lock:
            row = self._db.execute(
                "SELECT last_retrieved_utc FROM fetches WHERE source_id = ? AND fetch_key = ?",
                (source_id, fetch_key),
            ).fetchone()
        return None if row is None else parse_utc(row[0])

    def mark_fetched(self, source_id: str, fetch_key: str, payload: bytes) -> None:
        digest = hashlib.sha256(payload).hexdigest()
        with self._lock:
            self._db.execute(
                "INSERT INTO fetches VALUES (?, ?, ?, ?) ON CONFLICT (source_id, fetch_key) "
                "DO UPDATE SET last_retrieved_utc = excluded.last_retrieved_utc, "
                "payload_sha256 = excluded.payload_sha256",
                (source_id, fetch_key, utc_text(self._clock()), digest),
            )

    # --- records -----------------------------------------------------------------------

    def put(
        self,
        source_id: str,
        record_id: str,
        document: dict[str, Any],
        published_at_utc: str | None,
        parser_version: str,
    ) -> None:
        """Store a record; an unchanged revision only has its retrieval time updated."""
        digest = checksum(document)
        now = utc_text(self._clock())
        with self._lock:
            updated = self._db.execute(
                "UPDATE records SET last_retrieved_utc = ? "
                "WHERE source_id = ? AND record_id = ? AND checksum = ?",
                (now, source_id, record_id, digest),
            ).rowcount
            if updated == 0:
                self._db.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        source_id,
                        record_id,
                        digest,
                        published_at_utc,
                        now,
                        now,
                        parser_version,
                        json.dumps(document, sort_keys=True),
                    ),
                )

    def records(
        self, source_id: str, record_prefix: str, as_of: datetime | None = None
    ) -> list[SourceRecord]:
        """One revision per record whose ID starts with `record_prefix`.

        The revision is the latest one this system had observed by `as_of` when there is
        one (a true point-in-time view), otherwise the latest stored revision.
        """
        pattern = record_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            rows = self._db.execute(
                "SELECT record_id, checksum, published_at_utc, first_observed_utc, "
                "last_retrieved_utc, parser_version, document FROM records "
                "WHERE source_id = ? AND record_id LIKE ? ESCAPE '\\' "
                "ORDER BY record_id, first_observed_utc, rowid",
                (source_id, pattern + "%"),
            ).fetchall()
        grouped: dict[str, list[tuple[Any, ...]]] = {}
        for row in rows:
            grouped.setdefault(row[0], []).append(row)
        chosen: list[SourceRecord] = []
        for record_id, revisions in grouped.items():
            pick = revisions[-1]
            if as_of is not None:
                seen = [r for r in revisions if parse_utc(r[3]) <= as_of]
                if seen:
                    pick = seen[-1]
            chosen.append(
                SourceRecord(
                    source_id,
                    record_id,
                    pick[1],
                    pick[2],
                    revisions[0][3],
                    max(r[4] for r in revisions),
                    pick[5],
                    json.loads(pick[6]),
                    len(revisions),
                )
            )
        return chosen

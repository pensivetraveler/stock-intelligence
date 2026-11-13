"""Trading halts for the stage 3 flow watch (ADR 0057 §3): spans from SIP status messages.

Alpaca documents a `statuses` channel on the SIP stream (`wss://stream.data.alpaca.markets/
v2/sip`) whose messages (`T: "s"`) carry a status code `sc` per tape:

- Tape A & B (CTA): `2` trading halt, `3` resume. Other CTA codes (price indication,
  imbalances, short sale restriction, `F` limit up-limit down) do not open or close a halt.
- Tape C & O (UTP): `H` trading halt, `P` volatility trading pause, `Q` quotation
  resumption (quotes only, trading still halted), `T` trading resumption.

Measured 2026-11-12 09:14 ET with the user's key (read only, nothing stored): the account
receives the channel, and a CTA halt (`sc` `2`, tape B) repeats every five seconds while it
lasts. No UTP message arrived in that minute, so the UTP codes above are from the
documentation only. Alpaca documents no REST endpoint for past statuses: a halt is known
only if a listener was connected while it happened (or, for a CTA halt, within five
seconds of connecting). `siw-collector watch-halts` (services/collector/halt_watch.py) is
that listener; `recorded_halts` reads what it stored. Nothing in this module connects.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PARSER_VERSION = "sip-status-halts-1"
HALT_CODES = frozenset({"2", "H", "P"})
RESUME_CODES = frozenset({"3", "T"})
# CTA tapes repeat a halt every five seconds while it lasts; UTP tapes were not measured.
CTA_TAPES = frozenset({"A", "B"})
# A new listen that hears no repeat of an open CTA halt within this long ends it.
CONFIRM_SECONDS = 30.0
# An unclosed listen covers up to this long after its last heartbeat (written every 30 s).
ALIVE_SECONDS = 90.0
# Shorter holes in the record (a reconnect) are not reported as gaps.
GAP_TOLERANCE_SECONDS = 30.0


@dataclass(frozen=True)
class HaltSpan:
    """A halt from `start_utc` until `end_utc` (None: not resumed by the last message)."""

    start_utc: datetime
    end_utc: datetime | None
    code: str
    reason: str = ""

    def overlaps(self, start: datetime, end: datetime, as_of: datetime) -> bool:
        """Whether the halt covered any part of `[start, end)` as known at `as_of`."""
        if self.start_utc > as_of:
            return False
        stop = self.end_utc if self.end_utc is not None and self.end_utc <= as_of else as_of
        return self.start_utc < end and stop > start

    def open_at(self, moment: datetime) -> bool:
        return self.start_utc <= moment and (self.end_utc is None or self.end_utc > moment)


@dataclass
class HaltRead:
    """What is known about one symbol's halts in a session.

    `available` is False when nothing listened to the status channel for that session:
    the flow watch then cannot leave halted minutes out, and says so.
    """

    available: bool
    spans: list[HaltSpan] = field(default_factory=list)
    source: str = "none"
    note: str = ""
    #: Parts of the session up to the evaluation that no listener covered: a halt in them
    #: is unknown, and their empty minutes still count as failing minutes.
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)


def _moment(value: Any) -> datetime:
    """An RFC 3339 timestamp with up to nanoseconds, as a UTC datetime (microseconds)."""
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1]
    head, _, fraction = text.partition(".")
    micro = (fraction + "000000")[:6] if fraction else "000000"
    return datetime.fromisoformat(f"{head}.{micro}").replace(tzinfo=timezone.utc)


def spans_from_statuses(messages: Iterable[Mapping[str, Any]]) -> list[HaltSpan]:
    """Halt spans of one symbol from its status messages, oldest first.

    A repeated halt message extends nothing (the span is already open); a resume with no
    open halt is ignored; a halt still open after the last message has no end.
    """
    ordered = sorted(
        (m for m in messages if m.get("T", "s") == "s" and "t" in m and "sc" in m),
        key=lambda m: _moment(m["t"]),
    )
    spans: list[HaltSpan] = []
    opened: tuple[datetime, str, str] | None = None
    for message in ordered:
        code = str(message["sc"]).strip()
        moment = _moment(message["t"])
        if code in HALT_CODES and opened is None:
            opened = (moment, code, str(message.get("rm") or message.get("rc") or "").strip())
        elif code in RESUME_CODES and opened is not None:
            spans.append(HaltSpan(opened[0], moment, opened[1], opened[2]))
            opened = None
    if opened is not None:
        spans.append(HaltSpan(opened[0], None, opened[1], opened[2]))
    return spans


def _text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class _Listen:
    listen_id: int
    start: datetime
    end: datetime


def _coverage_gaps(
    listens: list[_Listen], low: datetime, high: datetime
) -> list[tuple[datetime, datetime]]:
    gaps: list[tuple[datetime, datetime]] = []
    reached = low
    for listen in sorted(listens, key=lambda item: item.start):
        if listen.start > reached:
            gaps.append((reached, min(listen.start, high)))
        reached = max(reached, listen.end)
        if reached >= high:
            break
    if reached < high:
        gaps.append((reached, high))
    tolerance = timedelta(seconds=GAP_TOLERANCE_SECONDS)
    return [(start, end) for start, end in gaps if end - start > tolerance]


def spans_from_listens(listens: list[_Listen], rows: list[Mapping[str, Any]]) -> list[HaltSpan]:
    """Spans of one symbol from its recorded changes, listen by listen.

    Within a listen this is `spans_from_statuses`. Across listens a halt stays open, unless
    it is a CTA halt and the next listen heard no repeat of it within CONFIRM_SECONDS:
    CTA halts repeat every five seconds, so it ended (at the latest) when that listen began.
    """
    spans: list[HaltSpan] = []
    opened: tuple[datetime, str, str, str] | None = None  # start, code, reason, tape
    for listen in sorted(listens, key=lambda item: item.start):
        own = sorted(
            (row for row in rows if int(row["listen_id"]) == listen.listen_id),
            key=lambda row: _moment(row["exchange_time"]),
        )
        confirm_until = listen.start + timedelta(seconds=CONFIRM_SECONDS)
        if opened is not None and opened[3] in CTA_TAPES and listen.end >= confirm_until:
            repeated = any(
                str(row["status_code"]) in HALT_CODES
                and _moment(row["received_at_utc"]) <= confirm_until
                for row in own
            )
            if not repeated:
                spans.append(HaltSpan(opened[0], listen.start, opened[1], opened[2]))
                opened = None
        for row in own:
            code = str(row["status_code"]).strip()
            moment = _moment(row["exchange_time"])
            if code in HALT_CODES and opened is None:
                reason = str(row["reason_message"] or row["reason_code"] or "").strip()
                opened = (moment, code, reason, str(row["tape"] or ""))
            elif code in RESUME_CODES and opened is not None:
                spans.append(HaltSpan(opened[0], moment, opened[1], opened[2]))
                opened = None
    if opened is not None:
        spans.append(HaltSpan(opened[0], None, opened[1], opened[2]))
    return spans


GetHaltRead = Callable[[str, Any, datetime], HaltRead]


def recorded_halts(path: Path) -> GetHaltRead:
    """Halts as recorded by `siw-collector watch-halts` in `path`, known at `as_of`.

    Only what was received by `as_of` is used, so a past evaluation is not changed by what
    the listener heard afterwards.
    """

    def read(symbol: str, window: Any, as_of: datetime) -> HaltRead:
        if not path.is_file():
            return HaltRead(
                available=False,
                source="sip_status_watch",
                note="halt 기록 파일이 없다: siw-collector watch-halts가 돈 적이 없다.",
            )
        low = window.pre_open_utc
        high = min(as_of, window.post_close_utc)
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute(
                "SELECT listen_id, started_at_utc, last_heard_utc, ended_at_utc FROM listens "
                "WHERE started_at_utc <= ? ORDER BY started_at_utc",
                (_text(high),),
            ).fetchall()
            listens: list[_Listen] = []
            alive = timedelta(seconds=ALIVE_SECONDS)
            for row in rows:
                start = _moment(row["started_at_utc"])
                if row["ended_at_utc"] is not None:
                    end = _moment(row["ended_at_utc"])
                else:
                    end = _moment(row["last_heard_utc"]) + alive
                end = min(end, as_of)
                if end > low - timedelta(minutes=10) and start < high:
                    listens.append(_Listen(int(row["listen_id"]), start, end))
            if not listens:
                return HaltRead(
                    available=False,
                    source="sip_status_watch",
                    note="이 세션에는 halt 기록기가 듣지 않았다.",
                )
            marks = ",".join("?" for _ in listens)
            changes = db.execute(
                f"SELECT * FROM status_changes WHERE symbol = ? AND listen_id IN ({marks}) "
                "AND received_at_utc <= ? ORDER BY change_id",
                (symbol, *[item.listen_id for item in listens], _text(as_of)),
            ).fetchall()
        finally:
            db.close()
        spans = [
            span
            for span in spans_from_listens(listens, [dict(row) for row in changes])
            if span.end_utc is None or span.end_utc > low
        ]
        gaps = _coverage_gaps(listens, low, high)
        note = "Alpaca SIP statuses, siw-collector watch-halts 기록"
        if gaps:
            note += f"; 듣지 않은 구간 {len(gaps)}개"
        return HaltRead(
            available=True, spans=spans, source="sip_status_watch", note=note, gaps=gaps
        )

    return read

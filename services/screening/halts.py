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
seconds of connecting). Nothing in this module connects; it turns recorded messages into
spans.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

PARSER_VERSION = "sip-status-halts-1"
HALT_CODES = frozenset({"2", "H", "P"})
RESUME_CODES = frozenset({"3", "T"})


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

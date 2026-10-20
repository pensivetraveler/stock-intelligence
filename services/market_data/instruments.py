"""Instrument IDs and the market each belongs to.

US listings: `us:<ticker>:<xnas|xnys|arcx|xase|bats>:2026` (ADR 0016).
Korean listings: `kr:<6-character KRX code>:<xkrx|xkos>:2026` - XKRX for KOSPI, XKOS for
KOSDAQ, both on the KRX trading schedule. The `:2026` suffix is the identity namespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from services.market_data.calendar import ExchangeCalendar

US_ID = re.compile(r"^us:([a-z][a-z0-9.]{0,9}):(xnas|xnys|arcx|xase|bats):2026$")
KR_ID = re.compile(r"^kr:([0-9a-z]{6}):(xkrx|xkos):2026$")
KR_CODE = re.compile(r"^[0-9A-Z]{6}$")
Market = Literal["US", "KR"]


@dataclass(frozen=True)
class ParsedInstrument:
    market: Market
    symbol: str  # ticker (US) or KRX code (KR), upper case
    venue: str  # MIC, upper case


def parse_instrument(instrument_id: str) -> ParsedInstrument | None:
    """The market, symbol and venue of a listed-equity ID; None for any other ID."""
    match = US_ID.fullmatch(instrument_id)
    if match:
        return ParsedInstrument("US", match.group(1).upper(), match.group(2).upper())
    match = KR_ID.fullmatch(instrument_id)
    if match:
        return ParsedInstrument("KR", match.group(1).upper(), match.group(2).upper())
    return None


def market_of(instrument_id: str) -> Market:
    """KR for Korean listings; US for everything else (configured US instruments too)."""
    parsed = parse_instrument(instrument_id)
    return parsed.market if parsed is not None else "US"


def kr_instrument_id(code: str, venue: str) -> str:
    if not KR_CODE.fullmatch(code) or venue not in ("XKRX", "XKOS"):
        raise ValueError(f"not a Korean listing: {code}:{venue}")
    return f"kr:{code.lower()}:{venue.lower()}:2026"


def calendar_for(instrument_id: str) -> ExchangeCalendar:
    """The exchange calendar an instrument trades on."""
    parsed = parse_instrument(instrument_id)
    if parsed is not None and parsed.market == "KR":
        return ExchangeCalendar(parsed.venue)
    return ExchangeCalendar("XNAS")

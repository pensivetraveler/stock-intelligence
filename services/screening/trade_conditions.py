"""Which Alpaca SIP trades count, and how (ADR 0052 §5 S2, `off-exchange-conditions-1`).

Shared by check D (`off_exchange.py`) and the structure model's session cache
(`structure/sessions.py`), so both count the same trades.
"""

from __future__ import annotations

CONDITION_POLICY_VERSION = "off-exchange-conditions-1"
OFF_EXCHANGE_CODE = "D"
SOURCE_ID = "alpaca-sip"

#: `u` values that mean the trade no longer stands (Alpaca trade schema).
REMOVED_UPDATES = frozenset({"canceled", "incorrect"})
#: Official open/close and corrected-close reports repeat prices, not new volume.
NOT_TRADES = frozenset({"M", "Q", "9"})
#: Real volume whose price does not show the market at that moment, so it is left out
#: of the price levels (it still counts toward volume shares): derivatively priced,
#: average price, prior reference price, qualified contingent, contingent, cash,
#: next day, seller, price variation.
OFF_MARKET_PRICE = frozenset({"4", "W", "P", "7", "V", "C", "N", "R", "H"})
#: `B` is "Average Price Trade" on tapes A/B but "Bunched Trade" on tape C.
AVERAGE_PRICE_ON_TAPES_AB = "B"
#: Reported late or out of sequence: the price is real, the timestamp is the report.
LATE_REPORT = frozenset({"Z", "L", "U"})

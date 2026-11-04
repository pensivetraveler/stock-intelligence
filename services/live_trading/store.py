"""The live-order ledger `SIW_DATA_DIR/live_orders.sqlite3` (ADR 0047 §2).

The paper store in another file, with its own kill switch and arming. It takes only
documents whose environment is `real`; the paper ledger takes only `demo` ones.
"""

from __future__ import annotations

from services.paper_trading.store import PaperOrderStore

DATABASE_NAME = "live_orders.sqlite3"


class LiveOrderStore(PaperOrderStore):
    database_name = DATABASE_NAME
    environment = "real"
    execution_ledger = "broker_live_orders"

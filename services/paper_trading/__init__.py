"""Paper-trading (모의투자) orders for SIW Agent, stage 1 of ADR 0036 (design: ADR 0037).

Everything here is broker-neutral except the paper-only HTTP client, which can reach
only each broker's demo domain. Paper intents, submissions and fills live in
`SIW_DATA_DIR/paper_orders.sqlite3` and are never mixed with real ledgers.
"""

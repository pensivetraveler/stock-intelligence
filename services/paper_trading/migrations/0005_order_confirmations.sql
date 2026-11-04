-- ADR 0047: a live order waits for the user's confirmation before it is sent. The table is
-- in every order ledger; only the live runner (live_orders.sqlite3) writes to it.
CREATE TABLE order_confirmations (
    intent_id TEXT PRIMARY KEY REFERENCES order_intents(intent_id),
    requested_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    decision TEXT CHECK (decision IN ('confirmed', 'declined')),
    decided_at_utc TEXT,
    source TEXT
);

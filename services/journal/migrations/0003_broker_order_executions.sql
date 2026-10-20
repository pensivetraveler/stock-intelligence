-- Executions reported by a broker API, one per order (the broker reports no individual
-- fills). A fourth ledger, separate from manual entries, statement imports and scenarios.
-- Append-only: an order's execution is stored once and never replaced.
CREATE TABLE broker_order_executions (
    execution_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    broker_account_ref TEXT NOT NULL,
    broker_order_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    executed_at_utc TEXT,
    synced_at_utc TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    document_json TEXT NOT NULL,
    UNIQUE (broker_id, broker_account_ref, broker_order_id)
);

CREATE INDEX broker_order_executions_instrument_idx ON broker_order_executions (instrument_id);

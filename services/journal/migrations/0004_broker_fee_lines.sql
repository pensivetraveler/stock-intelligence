-- Commission and tax a broker reports per settlement line without an order number
-- (Kiwoom US, ust21100; ADR 0029). Kept apart from order executions: positions add them
-- per account and instrument, and no order is given a fee it was not reported with.
-- Append-only: a line is stored once and never replaced.
CREATE TABLE broker_fee_lines (
    line_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    broker_account_ref TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    settlement_date TEXT NOT NULL,
    synced_at_utc TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    document_json TEXT NOT NULL
);

CREATE INDEX broker_fee_lines_instrument_idx ON broker_fee_lines (instrument_id);

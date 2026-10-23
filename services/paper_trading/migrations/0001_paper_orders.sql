-- Paper-trading (모의투자) orders, ADR 0037. A ledger of its own: nothing here is ever
-- read by the journal, reconciliation, trade review or broker positions. No keys, tokens
-- or account numbers are stored; accounts are referred to by a hash-based reference.

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    content_json TEXT NOT NULL,
    approved_at_utc TEXT NOT NULL
);

-- Intents are append-only: the outcome lives in intent_outcomes.
CREATE TABLE order_intents (
    intent_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES approvals(approval_id),
    broker_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    document_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE TRIGGER order_intents_no_update BEFORE UPDATE ON order_intents
BEGIN
    SELECT RAISE(ABORT, 'order intents are append-only');
END;

CREATE TRIGGER order_intents_no_delete BEFORE DELETE ON order_intents
BEGIN
    SELECT RAISE(ABORT, 'order intents are append-only');
END;

CREATE TABLE intent_outcomes (
    intent_id TEXT PRIMARY KEY REFERENCES order_intents(intent_id),
    status TEXT NOT NULL CHECK (status IN ('submitted', 'blocked', 'expired')),
    reason TEXT,
    decided_at_utc TEXT NOT NULL
);

CREATE TABLE order_submissions (
    submission_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL UNIQUE REFERENCES order_intents(intent_id),
    status TEXT NOT NULL
        CHECK (status IN ('pending_response', 'accepted', 'rejected', 'unknown')),
    requested_at_utc TEXT NOT NULL,
    document_json TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX order_submissions_status_idx ON order_submissions (status);
CREATE INDEX order_submissions_requested_idx ON order_submissions (requested_at_utc);

CREATE TABLE paper_executions (
    execution_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    broker_account_ref TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    document_json TEXT NOT NULL,
    synced_at_utc TEXT NOT NULL
);

CREATE TABLE limit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL REFERENCES order_intents(intent_id),
    reasons_json TEXT NOT NULL,
    at_utc TEXT NOT NULL
);

-- One row: the kill switch and arming state, read again just before every submission.
CREATE TABLE control_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    halted INTEGER NOT NULL CHECK (halted IN (0, 1)),
    halted_reason TEXT,
    armed INTEGER NOT NULL CHECK (armed IN (0, 1)),
    approval_id TEXT REFERENCES approvals(approval_id),
    last_heartbeat_utc TEXT,
    updated_at_utc TEXT NOT NULL,
    CHECK (armed = 0 OR (halted = 0 AND approval_id IS NOT NULL)),
    CHECK ((halted = 1) = (halted_reason IS NOT NULL))
);

INSERT INTO control_state (id, halted, halted_reason, armed, approval_id, updated_at_utc)
VALUES (1, 0, NULL, 0, NULL, '1970-01-01T00:00:00Z');

CREATE TABLE control_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL CHECK (event IN ('halt', 'release', 'arm', 'disarm')),
    reason TEXT,
    source TEXT NOT NULL,
    approval_id TEXT,
    at_utc TEXT NOT NULL
);

-- Actual (user-asserted) and hypothetical records live in separate tables so that
-- no query can mix them into one P&L. Both are append-only.
CREATE TABLE manual_executions (
    execution_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    executed_at_utc TEXT,
    asserted_at_utc TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    document_json TEXT NOT NULL
);

CREATE INDEX manual_executions_instrument_idx ON manual_executions (instrument_id);

CREATE TABLE scenarios (
    scenario_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    instrument_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    document_json TEXT NOT NULL,
    PRIMARY KEY (scenario_id, version)
);

CREATE INDEX scenarios_instrument_idx ON scenarios (instrument_id);

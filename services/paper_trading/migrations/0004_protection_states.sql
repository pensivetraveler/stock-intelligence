-- ADR 0044 G2: the protective levels of a paper position, kept so a restart goes on from
-- the last bar judged. At most one open state per account and instrument.
CREATE TABLE protection_states (
    intent_id TEXT PRIMARY KEY REFERENCES order_intents(intent_id),
    broker_account_ref TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    state_json TEXT NOT NULL,
    closed_reason TEXT,
    updated_at_utc TEXT NOT NULL
);
CREATE UNIQUE INDEX protection_states_one_open_idx
    ON protection_states (broker_account_ref, instrument_id) WHERE status = 'open';

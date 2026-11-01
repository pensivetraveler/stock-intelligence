-- ADR 0044 G1: the runner cancels orders left unfilled, so a submission can end cancelled.
-- SQLite cannot change a CHECK in place: the table is rebuilt with its rows.
CREATE TABLE order_submissions_new (
    submission_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL UNIQUE REFERENCES order_intents(intent_id),
    status TEXT NOT NULL
        CHECK (status IN ('pending_response', 'accepted', 'rejected', 'unknown', 'cancelled')),
    requested_at_utc TEXT NOT NULL,
    document_json TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
INSERT INTO order_submissions_new SELECT * FROM order_submissions;
DROP TABLE order_submissions;
ALTER TABLE order_submissions_new RENAME TO order_submissions;
CREATE INDEX order_submissions_status_idx ON order_submissions (status);
CREATE INDEX order_submissions_requested_idx ON order_submissions (requested_at_utc);

CREATE TABLE order_cancel_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL REFERENCES order_submissions(submission_id),
    requested_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'unknown')),
    cancel_order_ref TEXT,
    response_code TEXT,
    response_message TEXT
);
CREATE INDEX order_cancel_attempts_submission_idx ON order_cancel_attempts (submission_id);

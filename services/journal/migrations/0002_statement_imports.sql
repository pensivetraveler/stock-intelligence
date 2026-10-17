-- Broker statement imports: a third ledger, separate from manual and hypothetical
-- records. A batch is identified by the file's SHA-256, so a file imports once.
CREATE TABLE import_batches (
    batch_id TEXT PRIMARY KEY,
    file_sha256 TEXT NOT NULL UNIQUE,
    committed_at_utc TEXT NOT NULL,
    document_json TEXT NOT NULL
);

CREATE TABLE imported_executions (
    execution_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES import_batches (batch_id),
    instrument_id TEXT NOT NULL,
    broker_label TEXT NOT NULL,
    broker_execution_id TEXT,
    executed_at_utc TEXT NOT NULL,
    document_json TEXT NOT NULL
);

-- A broker's execution ID identifies one fill: the same ID can never be imported twice.
CREATE UNIQUE INDEX imported_native_id_idx
    ON imported_executions (broker_label, broker_execution_id)
    WHERE broker_execution_id IS NOT NULL;

CREATE INDEX imported_instrument_idx ON imported_executions (instrument_id);

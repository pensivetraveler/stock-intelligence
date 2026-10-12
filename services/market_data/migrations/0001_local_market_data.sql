CREATE TABLE dataset_revisions (
    source_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    dataset_revision TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (source_id, instrument_id, timeframe, adjustment, dataset_revision)
);

CREATE TABLE coverage_manifests (
    coverage_key TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    requested_start_utc TEXT NOT NULL,
    requested_end_utc TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_feed_scope TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    dataset_revision TEXT NOT NULL,
    point_in_time_cutoff_utc TEXT,
    coverage_status TEXT NOT NULL CHECK (coverage_status IN ('complete', 'partial', 'empty')),
    verification_status TEXT NOT NULL CHECK (verification_status IN ('unverified', 'verified')),
    manifest_json TEXT NOT NULL,
    parquet_paths_json TEXT NOT NULL,
    checked_at_utc TEXT NOT NULL,
    FOREIGN KEY (source_id, instrument_id, timeframe, adjustment, dataset_revision)
        REFERENCES dataset_revisions (
            source_id, instrument_id, timeframe, adjustment, dataset_revision
        )
);

CREATE INDEX coverage_lookup_idx ON coverage_manifests (
    instrument_id,
    timeframe,
    requested_start_utc,
    requested_end_utc,
    source_id,
    dataset_revision,
    point_in_time_cutoff_utc
);

CREATE TABLE acquisition_jobs (
    job_key TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'interrupted')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    checkpoint_json TEXT,
    error_code TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE revision_pins (
    pin_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    dataset_revision TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY (source_id, instrument_id, timeframe, adjustment, dataset_revision)
        REFERENCES dataset_revisions (
            source_id, instrument_id, timeframe, adjustment, dataset_revision
        )
);

CREATE TABLE write_journal (
    journal_id TEXT PRIMARY KEY,
    coverage_key TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    files_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'renamed', 'committed', 'failed')),
    error TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

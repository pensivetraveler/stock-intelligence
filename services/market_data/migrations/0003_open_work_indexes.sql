-- Opening a store looks for writes and jobs that a dead process left unfinished.
-- Without these, that look scanned every finished journal row (hundreds of MB of
-- manifests and file lists in a long-lived store) on each start, which took minutes
-- when the disk cache was cold. Partial indexes hold only the unfinished rows.
CREATE INDEX write_journal_open_idx ON write_journal(state)
    WHERE state IN ('pending', 'renamed');
CREATE INDEX acquisition_jobs_running_idx ON acquisition_jobs(status)
    WHERE status = 'running';

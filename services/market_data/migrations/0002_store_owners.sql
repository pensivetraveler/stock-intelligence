-- Rows written by a live store instance carry its owner ID so that crash
-- recovery in another process only touches work whose owner has exited.
-- NULL marks rows written before ownership tracking; they are treated as orphaned.
ALTER TABLE write_journal ADD COLUMN owner_id TEXT;
ALTER TABLE acquisition_jobs ADD COLUMN owner_id TEXT;

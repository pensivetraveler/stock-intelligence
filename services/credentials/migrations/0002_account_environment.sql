-- Whether a broker key belongs to a real (실전) or a demo (모의투자) account; vendor keys
-- (not per account) have none. Every broker key registered before this column existed
-- was used against the broker's real domain, so those keys are marked real.
ALTER TABLE credentials ADD COLUMN environment TEXT
    CHECK (environment IS NULL OR environment IN ('real', 'demo'));
UPDATE credentials SET environment = 'real' WHERE provider IN ('kis', 'toss', 'kiwoom', 'meritz');

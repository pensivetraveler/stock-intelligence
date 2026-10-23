-- Markets a broker's paper server was checked to take orders for (ADR 0037 P4). An
-- adapter offers only these markets; with none recorded, every paper order is blocked.
CREATE TABLE market_verifications (
    broker_id TEXT NOT NULL,
    market TEXT NOT NULL,
    evidence TEXT NOT NULL,
    verified_at_utc TEXT NOT NULL,
    PRIMARY KEY (broker_id, market)
);

-- Provider keys: only labels and ordering live here. Secret values are kept in the OS
-- keychain under the credential_id; this file never holds them or full account numbers.
CREATE TABLE credentials (
    credential_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    account_suffix TEXT CHECK (account_suffix IS NULL OR length(account_suffix) = 4),
    priority INTEGER NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    secret_revision INTEGER NOT NULL CHECK (secret_revision >= 1),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE (provider, label)
);

CREATE INDEX credentials_provider_idx ON credentials (provider, priority);

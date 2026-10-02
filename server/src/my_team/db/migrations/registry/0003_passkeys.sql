CREATE TABLE passkeys (
    id TEXT PRIMARY KEY,
    credential_id TEXT NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT '',
    created_ms INTEGER NOT NULL
);

CREATE TABLE init_claims (
  repo_key TEXT PRIMARY KEY,
  holder_session_id TEXT NOT NULL,
  epoch INTEGER NOT NULL DEFAULT 1,
  claimed_ms INTEGER NOT NULL
);

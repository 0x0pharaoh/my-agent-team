CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    ticket_id TEXT REFERENCES tickets(id),
    kind TEXT NOT NULL DEFAULT 'ticket' CHECK (kind IN ('ticket', 'reply')),
    step_index INTEGER NOT NULL DEFAULT 0,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    agent_type TEXT NOT NULL,
    session_id TEXT REFERENCES sessions(id),
    native_session_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'stopped', 'budget_exhausted')),
    worktree_path TEXT,
    branch TEXT,
    pid INTEGER,
    max_tokens INTEGER NOT NULL,
    max_seconds INTEGER NOT NULL,
    tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    summary TEXT,
    error TEXT,
    created_ms INTEGER NOT NULL,
    started_ms INTEGER,
    ended_ms INTEGER
);
CREATE UNIQUE INDEX runs_one_active_per_ticket ON runs(ticket_id) WHERE status IN ('queued', 'running') AND kind = 'ticket';
CREATE INDEX runs_by_ticket ON runs(ticket_id, created_ms);
ALTER TABLE tickets ADD COLUMN max_tokens INTEGER;
ALTER TABLE tickets ADD COLUMN max_minutes INTEGER;
ALTER TABLE questions ADD COLUMN run_id TEXT REFERENCES runs(id);

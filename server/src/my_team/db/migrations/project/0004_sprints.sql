CREATE TABLE sprints (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'active', 'completed', 'cancelled')),
    start_ms INTEGER,
    end_ms INTEGER,
    review_summary TEXT,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL
);
CREATE UNIQUE INDEX sprints_one_active ON sprints(status) WHERE status = 'active';
ALTER TABLE tickets ADD COLUMN sprint_id TEXT REFERENCES sprints(id);
CREATE INDEX tickets_sprint ON tickets(sprint_id);

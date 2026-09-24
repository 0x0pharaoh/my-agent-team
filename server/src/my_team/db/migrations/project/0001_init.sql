CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    role TEXT,
    seat_no INTEGER NOT NULL,
    named INTEGER NOT NULL DEFAULT 0,
    created_ms INTEGER NOT NULL,
    archived_ms INTEGER,
    UNIQUE (agent_type, seat_no)
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    agent_type TEXT NOT NULL,
    native_session_id TEXT NOT NULL,
    predecessor_id TEXT REFERENCES sessions(id),
    root_path TEXT,
    started_ms INTEGER NOT NULL,
    last_heartbeat_ms INTEGER,
    last_activity_ms INTEGER NOT NULL,
    ended_ms INTEGER,
    notice_cursor INTEGER NOT NULL DEFAULT 0,
    context_sent INTEGER NOT NULL DEFAULT 0,
    UNIQUE (agent_type, native_session_id)
);

CREATE INDEX sessions_by_agent ON sessions(agent_id);

CREATE TABLE tickets (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL UNIQUE,
    key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'feature' CHECK (type IN ('feature', 'bug', 'chore', 'docs', 'spike')),
    status TEXT NOT NULL CHECK (status IN ('proposed', 'backlog', 'ready', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled')),
    priority TEXT NOT NULL DEFAULT 'p2' CHECK (priority IN ('p0', 'p1', 'p2', 'p3')),
    assignee_agent_id TEXT REFERENCES agents(id),
    active_session_id TEXT REFERENCES sessions(id),
    last_session_id TEXT REFERENCES sessions(id),
    revoked_session_id TEXT REFERENCES sessions(id),
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',
    changes_requested INTEGER NOT NULL DEFAULT 0,
    status_reason TEXT,
    implementation_summary TEXT,
    origin_key TEXT UNIQUE,
    created_by TEXT NOT NULL,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    CHECK ((status = 'in_progress') = (active_session_id IS NOT NULL))
);

CREATE UNIQUE INDEX tickets_one_active_per_session ON tickets(active_session_id) WHERE status = 'in_progress';
CREATE INDEX tickets_by_assignee ON tickets(assignee_agent_id, status);

CREATE TABLE ticket_links (
    from_id TEXT NOT NULL REFERENCES tickets(id),
    to_id TEXT NOT NULL REFERENCES tickets(id),
    type TEXT NOT NULL CHECK (type = 'blocks'),
    overridden_ms INTEGER,
    PRIMARY KEY (from_id, to_id, type),
    CHECK (from_id <> to_id)
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    session_id TEXT,
    payload TEXT NOT NULL,
    created_ms INTEGER NOT NULL
);

CREATE INDEX events_by_entity ON events(entity_type, entity_id, id);

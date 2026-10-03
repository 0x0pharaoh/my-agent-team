CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('fact', 'work_summary', 'note', 'human_instruction')),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '',
    ticket_id TEXT REFERENCES tickets(id),
    source TEXT NOT NULL CHECK (source IN ('agent_reported', 'human_confirmed')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'retracted', 'needs_review')),
    supersedes_id TEXT REFERENCES memories(id),
    content_hash TEXT NOT NULL,
    author_type TEXT NOT NULL,
    author_id TEXT NOT NULL,
    session_id TEXT,
    expires_ms INTEGER,
    created_ms INTEGER NOT NULL
);

CREATE INDEX memories_by_hash ON memories(content_hash);
CREATE INDEX memories_by_author ON memories(author_id, created_ms);

CREATE VIRTUAL TABLE memories_fts USING fts5(title, body, tags, content='memories', content_rowid='rowid');

CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts (rowid, title, body, tags) VALUES (new.rowid, new.title, new.body, new.tags);
END;

CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, title, body, tags) VALUES ('delete', old.rowid, old.title, old.body, old.tags);
END;

CREATE TRIGGER memories_au AFTER UPDATE OF title, body, tags ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, title, body, tags) VALUES ('delete', old.rowid, old.title, old.body, old.tags);
    INSERT INTO memories_fts (rowid, title, body, tags) VALUES (new.rowid, new.title, new.body, new.tags);
END;

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    parent_id TEXT REFERENCES messages(id),
    channel TEXT NOT NULL CHECK (channel IN ('direct', 'ticket', 'broadcast', 'human')),
    ticket_id TEXT REFERENCES tickets(id),
    sender_type TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    sender_session_id TEXT,
    body TEXT NOT NULL,
    requires_response INTEGER NOT NULL DEFAULT 0,
    created_ms INTEGER NOT NULL
);

CREATE INDEX messages_by_thread ON messages(thread_id, created_ms);

CREATE TABLE message_recipients (
    message_id TEXT NOT NULL REFERENCES messages(id),
    recipient_type TEXT NOT NULL CHECK (recipient_type IN ('agent', 'human')),
    recipient_id TEXT NOT NULL,
    surfaced_ms INTEGER,
    read_ms INTEGER,
    PRIMARY KEY (message_id, recipient_type, recipient_id)
);

CREATE INDEX recipients_unread ON message_recipients(recipient_type, recipient_id, read_ms);

CREATE TABLE questions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('question', 'decision')),
    prompt TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '[]',
    recommendation TEXT,
    answer TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'rejected')),
    ticket_id TEXT REFERENCES tickets(id),
    asked_by_agent TEXT REFERENCES agents(id),
    asked_by_session TEXT,
    created_ms INTEGER NOT NULL,
    answered_ms INTEGER
);

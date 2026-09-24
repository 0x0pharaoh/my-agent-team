CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    key TEXT NOT NULL,
    created_ms INTEGER NOT NULL
);

CREATE TABLE project_roots (
    repo_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    root_path TEXT NOT NULL,
    git_common_dir TEXT,
    bound_ms INTEGER NOT NULL
);

CREATE INDEX project_roots_by_project ON project_roots(project_id);

CREATE TABLE human_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    passphrase_hash TEXT NOT NULL,
    updated_ms INTEGER NOT NULL
);

CREATE TABLE human_sessions (
    token_hash TEXT PRIMARY KEY,
    created_ms INTEGER NOT NULL,
    expires_ms INTEGER NOT NULL,
    revoked_ms INTEGER
);

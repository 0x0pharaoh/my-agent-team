CREATE TABLE doc_proposals (
  id TEXT PRIMARY KEY,
  doc TEXT NOT NULL,
  anchor TEXT NOT NULL,
  class TEXT NOT NULL,
  base_section_hash TEXT,
  base_file_hash TEXT,
  content TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending',
  target_hash TEXT,
  author_session_id TEXT,
  created_ms INTEGER NOT NULL,
  decided_ms INTEGER
);
CREATE INDEX doc_proposals_status ON doc_proposals(status);

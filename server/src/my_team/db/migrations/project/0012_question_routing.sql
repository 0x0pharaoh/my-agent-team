ALTER TABLE questions ADD COLUMN routed_to_agent_id TEXT REFERENCES agents(id);
ALTER TABLE questions ADD COLUMN escalated_ms INTEGER;
ALTER TABLE runs ADD COLUMN question_id TEXT REFERENCES questions(id);

ALTER TABLE runs ADD COLUMN message_id TEXT REFERENCES messages(id);
CREATE INDEX runs_by_message ON runs(message_id);

ALTER TABLE agents ADD COLUMN lead_id TEXT REFERENCES agents(id);

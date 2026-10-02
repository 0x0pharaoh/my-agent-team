CREATE TABLE purge_flags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id TEXT NOT NULL,
  backup_name TEXT NOT NULL,
  flagged_ms INTEGER NOT NULL
);

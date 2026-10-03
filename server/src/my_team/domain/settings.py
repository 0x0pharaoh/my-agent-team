import json

from my_team.actor import Actor
from my_team.db.engine import Tx
from my_team.domain import events

DEFAULTS = {"auto_run": False, "default_max_tokens": 300_000, "default_max_minutes": 30, "max_parallel_runs": 2}


def get(tx: Tx) -> dict:
    stored = {row["key"].removeprefix("setting."): json.loads(row["value"])
              for row in tx.all("SELECT key, value FROM meta WHERE key LIKE 'setting.%'")}
    return DEFAULTS | {key: value for key, value in stored.items() if key in DEFAULTS}


def update(tx: Tx, actor: Actor, now: int, changes: dict) -> dict:
    for key, value in changes.items():
        tx.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (f"setting.{key}", json.dumps(value)))
    events.emit(tx, "settings.updated", "project", "settings", actor, changes, now)
    return get(tx)

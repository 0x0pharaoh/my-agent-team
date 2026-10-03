import json

from my_team.actor import Actor
from my_team.db.engine import Tx


def emit(tx: Tx, type_: str, entity_type: str, entity_id: str, actor: Actor, payload: dict, now: int) -> None:
    cursor = tx.execute(
        "INSERT INTO events (type, entity_type, entity_id, actor_type, actor_id, session_id, payload, created_ms)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (type_, entity_type, entity_id, actor.kind, actor.id, actor.session_id,
         json.dumps(payload, separators=(",", ":")), now),
    )
    tx.events.append({"id": cursor.lastrowid, "type": type_, "entity_type": entity_type, "entity_id": entity_id,
                      "actor_type": actor.kind, "actor_id": actor.id, "session_id": actor.session_id,
                      "payload": payload, "created_ms": now})


def _decode(row) -> dict:
    event = dict(row)
    event["payload"] = json.loads(event["payload"])
    return event


def after(tx: Tx, after_id: int, limit: int = 500) -> list[dict]:
    rows = tx.all("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit))
    return [_decode(row) for row in rows]


def for_entity(tx: Tx, entity_type: str, entity_id: str) -> list[dict]:
    rows = tx.all("SELECT * FROM events WHERE entity_type = ? AND entity_id = ? ORDER BY id", (entity_type, entity_id))
    return [_decode(row) for row in rows]


def latest_id(tx: Tx) -> int:
    return tx.scalar("SELECT COALESCE(MAX(id), 0) FROM events")

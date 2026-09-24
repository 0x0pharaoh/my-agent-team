from my_team.actor import SYSTEM, Actor
from my_team.db.engine import Tx
from my_team.domain import events
from my_team.errors import NotFound
from my_team.ids import new_id

HEARTBEAT_INTERVAL_S = 30
OFFLINE_AFTER_MS = 90_000
IDLE_AFTER_MS = 5 * 60_000
STALL_AFTER_MS = 15 * 60_000
RESTART_GRACE_MS = 2 * 60_000


def liveness(row, now: int, daemon_started_ms: int) -> str:
    if row["ended_ms"] is not None:
        return "offline"
    heartbeat = row["last_heartbeat_ms"]
    if heartbeat is None:
        return "unknown"
    if now - heartbeat > OFFLINE_AFTER_MS:
        return "unknown" if now - daemon_started_ms < RESTART_GRACE_MS else "offline"
    if now - row["last_activity_ms"] > IDLE_AFTER_MS:
        return "idle"
    return "active"


def stall_cutoff(now: int, daemon_started_ms: int) -> int:
    """Holders whose last heartbeat is older than this are stalled; nothing is stalled during restart grace."""
    if daemon_started_ms + RESTART_GRACE_MS > now - STALL_AFTER_MS:
        return -1
    return now - STALL_AFTER_MS - OFFLINE_AFTER_MS


def get(tx: Tx, session_id: str):
    row = tx.one("SELECT s.*, a.display_name AS agent_name FROM sessions s JOIN agents a ON a.id = s.agent_id"
                 " WHERE s.id = ?", (session_id,))
    if row is None:
        raise NotFound("unknown_session", "This session is not registered with the project.")
    return row


def _named_agent(tx: Tx, agent_type: str, name: str) -> str:
    row = tx.one("SELECT id FROM agents WHERE agent_type = ? AND display_name = ? AND archived_ms IS NULL",
                 (agent_type, name))
    if row is None:
        raise NotFound("unknown_agent", f"No agent named {name!r} of type {agent_type}.", agent=name)
    return row["id"]


def _free_seat(tx: Tx, agent_type: str, now: int) -> str:
    row = tx.one(
        "SELECT a.id FROM agents a WHERE a.agent_type = ? AND a.named = 0 AND a.archived_ms IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.agent_id = a.id AND s.ended_ms IS NULL"
        " AND COALESCE(s.last_heartbeat_ms, s.started_ms) > ?) ORDER BY a.seat_no LIMIT 1",
        (agent_type, now - OFFLINE_AFTER_MS),
    )
    if row:
        return row["id"]
    seat_no = tx.scalar("SELECT COALESCE(MAX(seat_no), 0) + 1 FROM agents WHERE agent_type = ?", (agent_type,))
    agent_id = new_id()
    name = agent_type if seat_no == 1 else f"{agent_type}-{seat_no}"
    tx.execute("INSERT INTO agents (id, display_name, agent_type, seat_no, created_ms) VALUES (?, ?, ?, ?, ?)",
               (agent_id, name, agent_type, seat_no, now))
    events.emit(tx, "agent.created", "agent", agent_id, SYSTEM, {"name": name, "agent_type": agent_type}, now)
    return agent_id


def register(tx: Tx, *, agent_type: str, native_id: str, root_path: str | None, now: int,
             agent_name: str | None = None, agent_id: str | None = None):
    existing = tx.one("SELECT id FROM sessions WHERE agent_type = ? AND native_session_id = ?", (agent_type, native_id))
    if existing:
        tx.execute("UPDATE sessions SET last_heartbeat_ms = ?, last_activity_ms = ?, ended_ms = NULL,"
                   " root_path = COALESCE(?, root_path) WHERE id = ?", (now, now, root_path, existing["id"]))
        return get(tx, existing["id"])
    if agent_id is None:
        agent_id = _named_agent(tx, agent_type, agent_name) if agent_name else _free_seat(tx, agent_type, now)
    session_id = new_id()
    tx.execute(
        "INSERT INTO sessions (id, agent_id, agent_type, native_session_id, root_path, started_ms,"
        " last_heartbeat_ms, last_activity_ms, notice_cursor)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(id), 0) FROM events))",
        (session_id, agent_id, agent_type, native_id, root_path, now, now, now),
    )
    row = get(tx, session_id)
    events.emit(tx, "session.started", "session", session_id, Actor("agent", agent_id, session_id, agent_id),
                {"agent": row["agent_name"], "agent_type": agent_type}, now)
    return row


def succeed(tx: Tx, old_session_id: str, new_native_id: str, now: int):
    old = get(tx, old_session_id)
    new = register(tx, agent_type=old["agent_type"], native_id=new_native_id, root_path=old["root_path"], now=now,
                   agent_id=old["agent_id"])
    if new["id"] == old["id"]:
        return new
    tx.execute("UPDATE sessions SET predecessor_id = COALESCE(predecessor_id, ?), context_sent = ?, notice_cursor = ?"
               " WHERE id = ?", (old["id"], old["context_sent"], old["notice_cursor"], new["id"]))
    tx.execute("UPDATE sessions SET ended_ms = ? WHERE id = ?", (now, old["id"]))
    tx.execute("UPDATE tickets SET active_session_id = ?, last_session_id = ?, claim_epoch = claim_epoch + 1,"
               " version = version + 1, updated_ms = ? WHERE active_session_id = ? AND status = 'in_progress'",
               (new["id"], new["id"], now, old["id"]))
    events.emit(tx, "session.succeeded", "session", new["id"], Actor("agent", new["agent_id"], new["id"], new["agent_id"]),
                {"predecessor": old["id"]}, now)
    return get(tx, new["id"])


def heartbeat(tx: Tx, session_id: str, now: int) -> None:
    if tx.execute("UPDATE sessions SET last_heartbeat_ms = ?, ended_ms = NULL WHERE id = ?",
                  (now, session_id)).rowcount == 0:
        raise NotFound("unknown_session", "This session is not registered with the project.")


def touch(tx: Tx, session_id: str, now: int) -> None:
    tx.execute("UPDATE sessions SET last_activity_ms = ? WHERE id = ?", (now, session_id))


def end(tx: Tx, session_id: str, now: int) -> None:
    row = get(tx, session_id)
    tx.execute("UPDATE sessions SET ended_ms = ? WHERE id = ?", (now, session_id))
    events.emit(tx, "session.ended", "session", session_id, Actor("agent", row["agent_id"], session_id, row["agent_id"]),
                {}, now)


def seats(tx: Tx, now: int, daemon_started_ms: int) -> list[dict]:
    agents = tx.all("SELECT * FROM agents WHERE archived_ms IS NULL ORDER BY agent_type, seat_no")
    rows = tx.all(
        "SELECT s.*, t.key AS ticket_key FROM sessions s"
        " LEFT JOIN tickets t ON t.active_session_id = s.id AND t.status = 'in_progress'"
        " WHERE s.ended_ms IS NULL OR s.ended_ms > ? ORDER BY s.started_ms DESC",
        (now - 24 * 3600_000,),
    )
    by_agent: dict[str, list[dict]] = {}
    for row in rows:
        by_agent.setdefault(row["agent_id"], []).append({
            "id": row["id"], "native_session_id": row["native_session_id"], "status": liveness(row, now, daemon_started_ms),
            "ticket": row["ticket_key"],
            "started_ms": row["started_ms"], "last_heartbeat_ms": row["last_heartbeat_ms"],
            "last_activity_ms": row["last_activity_ms"], "ended_ms": row["ended_ms"],
            "predecessor_id": row["predecessor_id"], "root_path": row["root_path"],
        })
    return [{"id": a["id"], "display_name": a["display_name"], "agent_type": a["agent_type"], "role": a["role"],
             "seat_no": a["seat_no"], "named": bool(a["named"]), "sessions": by_agent.get(a["id"], [])}
            for a in agents]

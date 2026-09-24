import json
import sqlite3

from my_team.actor import Actor
from my_team.db.engine import Tx
from my_team.domain import events
from my_team.errors import Conflict, Forbidden, Invalid, NotFound
from my_team.ids import new_id
from my_team.redact import redact

HUMAN_ACTIONS: dict[str, tuple[frozenset[str], str]] = {
    "accept": (frozenset({"proposed"}), "backlog"),
    "ready": (frozenset({"backlog"}), "ready"),
    "done": (frozenset({"in_review"}), "done"),
    "request_changes": (frozenset({"in_review"}), "ready"),
    "pause": (frozenset({"in_progress"}), "blocked"),
    "unblock": (frozenset({"blocked"}), "ready"),
    "cancel": (frozenset({"proposed", "backlog", "ready", "in_progress", "in_review", "blocked"}), "cancelled"),
}
AGENT_ACTIONS = ("note", "review", "block", "release")
CLOSED = ("done", "cancelled")

_SELECT = (
    "SELECT t.*, a.display_name AS assignee_name, s.last_heartbeat_ms AS holder_heartbeat_ms,"
    " ha.display_name AS holder_name FROM tickets t"
    " LEFT JOIN agents a ON a.id = t.assignee_agent_id"
    " LEFT JOIN sessions s ON s.id = t.active_session_id"
    " LEFT JOIN agents ha ON ha.id = s.agent_id"
)

_OPEN_BLOCKERS = (
    "SELECT b.key FROM ticket_links l JOIN tickets b ON b.id = l.from_id"
    " WHERE l.to_id = ? AND l.type = 'blocks' AND l.overridden_ms IS NULL AND b.status NOT IN ('done', 'cancelled')"
    " ORDER BY b.seq"
)

_CLAIM = """
UPDATE tickets SET status = 'in_progress', active_session_id = :session, last_session_id = :session,
    revoked_session_id = NULL, assignee_agent_id = :agent, claim_epoch = claim_epoch + 1,
    changes_requested = 0, status_reason = NULL, version = version + 1, updated_ms = :now
WHERE id = :id AND (
    (status = 'ready' AND (assignee_agent_id IS NULL OR assignee_agent_id = :agent)
        AND NOT EXISTS (SELECT 1 FROM ticket_links l JOIN tickets b ON b.id = l.from_id
            WHERE l.to_id = tickets.id AND l.type = 'blocks' AND l.overridden_ms IS NULL
            AND b.status NOT IN ('done', 'cancelled')))
    OR (status = 'in_progress' AND assignee_agent_id = :agent AND active_session_id <> :session
        AND (SELECT last_heartbeat_ms FROM sessions WHERE id = tickets.active_session_id) < :stall_cutoff)
)
"""


def _criteria(value) -> list[dict]:
    items = []
    for item in value or []:
        if isinstance(item, str):
            item = {"text": item, "done": False}
        text = redact(str(item.get("text", "")).strip())
        if text:
            items.append({"text": text, "done": bool(item.get("done", False))})
    return items


def _row(tx: Tx, key: str):
    row = tx.one(f"{_SELECT} WHERE t.key = ?", (key,))
    if row is None:
        raise NotFound("unknown_ticket", f"No ticket {key}.", ticket=key)
    return row


def serialize(tx: Tx, row, stall_cutoff: int) -> dict:
    stalled = (row["status"] == "in_progress" and row["holder_heartbeat_ms"] is not None
               and row["holder_heartbeat_ms"] < stall_cutoff)
    return {
        "id": row["id"], "key": row["key"], "title": row["title"], "description": row["description"],
        "type": row["type"], "status": row["status"], "priority": row["priority"],
        "assignee": {"id": row["assignee_agent_id"], "name": row["assignee_name"]} if row["assignee_agent_id"] else None,
        "holder": {"session_id": row["active_session_id"], "name": row["holder_name"],
                   "last_heartbeat_ms": row["holder_heartbeat_ms"]} if row["active_session_id"] else None,
        "claim_epoch": row["claim_epoch"],
        "acceptance_criteria": json.loads(row["acceptance_criteria"]),
        "changes_requested": bool(row["changes_requested"]),
        "status_reason": row["status_reason"], "implementation_summary": row["implementation_summary"],
        "origin_key": row["origin_key"], "version": row["version"], "paths": json.loads(row["paths"]),
        "created_ms": row["created_ms"], "updated_ms": row["updated_ms"],
        "pending_pickup": row["status"] == "ready" and row["assignee_agent_id"] is not None,
        "stalled": stalled,
        "waiting_on": [r["key"] for r in tx.all(_OPEN_BLOCKERS, (row["id"],))] if row["status"] not in CLOSED else [],
        "human_actions": [name for name, (sources, _) in HUMAN_ACTIONS.items() if row["status"] in sources],
    }


def get(tx: Tx, key: str, stall_cutoff: int) -> dict:
    return serialize(tx, _row(tx, key), stall_cutoff)


def find(tx: Tx, stall_cutoff: int, *, statuses: list[str] | None = None, assignee_agent_id: str | None = None,
         limit: int = 200) -> list[dict]:
    clauses, params = [], []
    if statuses:
        clauses.append(f"t.status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
    if assignee_agent_id:
        clauses.append("t.assignee_agent_id = ?")
        params.append(assignee_agent_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = tx.all(f"{_SELECT}{where} ORDER BY t.priority, t.seq LIMIT ?", (*params, limit))
    return [serialize(tx, row, stall_cutoff) for row in rows]


def create(tx: Tx, actor: Actor, project_key: str, now: int, *, title: str, description: str = "",
           type_: str = "feature", priority: str = "p2", acceptance_criteria=None, status: str | None = None,
           ticket_id: str | None = None, origin_key: str | None = None) -> str:
    title, description = redact(title.strip()), redact(description)
    if not title:
        raise Invalid("title_required", "A ticket needs a title.")
    if ticket_id:
        existing = tx.one("SELECT key, title FROM tickets WHERE id = ?", (ticket_id,))
        if existing:
            if existing["title"] != title:
                raise Conflict("id_reused", "This ticket id was already used for a different ticket.")
            return existing["key"]
    if origin_key:
        existing_key = tx.scalar("SELECT key FROM tickets WHERE origin_key = ?", (origin_key,))
        if existing_key:
            return existing_key
    criteria = _criteria(acceptance_criteria)
    if actor.is_agent:
        status = "proposed"
    elif status not in ("backlog", "ready"):
        status = "backlog"
    if status == "ready" and not criteria:
        raise Invalid("acceptance_criteria_required", "A ready ticket needs acceptance criteria.")
    seq = tx.scalar("SELECT COALESCE(MAX(seq), 0) + 1 FROM tickets")
    key = f"{project_key}-{seq}"
    ticket_id = ticket_id or new_id()
    tx.execute(
        "INSERT INTO tickets (id, seq, key, title, description, type, status, priority, acceptance_criteria,"
        " origin_key, created_by, created_ms, updated_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticket_id, seq, key, title, description, type_, status, priority, json.dumps(criteria), origin_key,
         actor.id, now, now),
    )
    events.emit(tx, "ticket.created", "ticket", ticket_id, actor, {"key": key, "title": title, "status": status}, now)
    return key


def _not_claimable(tx: Tx, row, actor: Actor, stall_cutoff: int) -> Conflict:
    if row["status"] == "in_progress":
        return Conflict("already_claimed_by", f"{row['key']} is held by {row['holder_name']}.",
                        ticket=row["key"], holder=row["holder_name"])
    if row["status"] == "ready" and row["assignee_agent_id"] not in (None, actor.agent_id):
        return Conflict("assigned_elsewhere", f"{row['key']} is assigned to {row['assignee_name']}.",
                        ticket=row["key"], assignee=row["assignee_name"])
    blockers = [r["key"] for r in tx.all(_OPEN_BLOCKERS, (row["id"],))]
    if row["status"] == "ready" and blockers:
        return Conflict("waiting_on", f"{row['key']} is waiting on {', '.join(blockers)}.",
                        ticket=row["key"], waiting_on=blockers)
    return Conflict("not_claimable", f"{row['key']} is {row['status']}; only ready tickets can be claimed.",
                    ticket=row["key"], status=row["status"])


def _paths(paths: list[str] | None) -> str | None:
    if paths is None:
        return None
    return json.dumps(list(dict.fromkeys(p.replace("\\", "/").strip() for p in paths if p.strip()))[:50])


def claim(tx: Tx, actor: Actor, key: str, now: int, stall_cutoff: int, paths: list[str] | None = None) -> dict:
    row = _row(tx, key)
    if row["status"] == "in_progress" and row["active_session_id"] == actor.session_id:
        if paths is not None:
            tx.execute("UPDATE tickets SET paths = ? WHERE id = ?", (_paths(paths), row["id"]))
        return get(tx, key, stall_cutoff)
    params = {"id": row["id"], "session": actor.session_id, "agent": actor.agent_id, "now": now,
              "stall_cutoff": stall_cutoff}
    try:
        changed = tx.execute(_CLAIM, params).rowcount
    except sqlite3.IntegrityError:
        held = tx.scalar("SELECT key FROM tickets WHERE active_session_id = ? AND status = 'in_progress'",
                         (actor.session_id,))
        raise Conflict("session_has_active_ticket",
                       f"This session already holds {held}. Record a note and release it or move it to review first.",
                       ticket=held) from None
    if not changed:
        raise _not_claimable(tx, row, actor, stall_cutoff)
    if paths is not None:
        tx.execute("UPDATE tickets SET paths = ? WHERE id = ?", (_paths(paths), row["id"]))
    claimed = _row(tx, key)
    kind = "ticket.taken_over" if row["status"] == "in_progress" else "ticket.claimed"
    events.emit(tx, kind, "ticket", row["id"], actor,
                {"key": key, "epoch": claimed["claim_epoch"], "previous_session": row["active_session_id"]}, now)
    return serialize(tx, claimed, stall_cutoff)


def act(tx: Tx, actor: Actor, key: str, action: str, epoch: int, now: int, stall_cutoff: int,
        note: str | None = None, summary: str | None = None, paths: list[str] | None = None) -> dict:
    if action not in AGENT_ACTIONS:
        raise Invalid("unknown_action", f"Action must be one of {', '.join(AGENT_ACTIONS)}.")
    row = _row(tx, key)
    if row["revoked_session_id"] == actor.session_id:
        raise Forbidden("ticket_revoked", f"STOP: {key} was taken off this session by the human. Stop editing, do not "
                        "commit, and post one note summarising where you left off.", ticket=key)
    if row["status"] != "in_progress" or row["active_session_id"] != actor.session_id:
        raise Forbidden("not_holder", f"This session does not hold {key}.", ticket=key)
    if row["claim_epoch"] != epoch:
        raise Conflict("claim_lost", f"Claim epoch {epoch} is stale for {key}; call ticket_find to refresh.",
                       ticket=key, epoch=row["claim_epoch"])
    note, summary = redact((note or "").strip()), redact((summary or "").strip())
    if paths is not None:
        tx.execute("UPDATE tickets SET paths = ? WHERE id = ?", (_paths(paths), row["id"]))
    if action == "note" and paths is not None and not note:
        return get(tx, key, stall_cutoff)
    if action == "note":
        if not note:
            raise Invalid("note_required", "A note needs text.")
        events.emit(tx, "ticket.note", "ticket", row["id"], actor, {"key": key, "note": note}, now)
        return serialize(tx, row, stall_cutoff)
    if action == "review" and not summary:
        raise Invalid("summary_required", "Moving to review needs an implementation summary.")
    if action in ("block", "release") and not note:
        raise Invalid("note_required", "Record where you left off before releasing or blocking the ticket.")
    target = {"review": "in_review", "block": "blocked", "release": "ready"}[action]
    tx.execute(
        "UPDATE tickets SET status = ?, active_session_id = NULL, status_reason = ?,"
        " implementation_summary = COALESCE(?, implementation_summary), version = version + 1, updated_ms = ?"
        " WHERE id = ? AND active_session_id = ? AND claim_epoch = ? AND status = 'in_progress'",
        (target, note or None, summary or None, now, row["id"], actor.session_id, epoch),
    )
    events.emit(tx, "ticket.transitioned", "ticket", row["id"], actor,
                {"key": key, "from": "in_progress", "to": target, "action": action, "note": note or None}, now)
    return get(tx, key, stall_cutoff)


def _check_version(row, version: int | None) -> None:
    if version is not None and version != row["version"]:
        raise Conflict("version_conflict", f"{row['key']} changed since you loaded it; reload and retry.",
                       ticket=row["key"], version=row["version"])


def _revoke(tx: Tx, row, actor: Actor, reason: str, now: int) -> None:
    if row["active_session_id"]:
        events.emit(tx, "ticket.revoked", "ticket", row["id"], actor,
                    {"key": row["key"], "session_id": row["active_session_id"], "reason": reason}, now)


def transition(tx: Tx, actor: Actor, key: str, action: str, now: int, stall_cutoff: int,
               version: int | None = None, reason: str | None = None) -> dict:
    if action not in HUMAN_ACTIONS:
        raise Invalid("unknown_action", f"Action must be one of {', '.join(HUMAN_ACTIONS)}.")
    sources, target = HUMAN_ACTIONS[action]
    row = _row(tx, key)
    _check_version(row, version)
    if row["status"] not in sources:
        raise Conflict("invalid_transition", f"{key} is {row['status']}; '{action}' does not apply.",
                       ticket=key, status=row["status"])
    if target == "ready" and row["status"] == "backlog" and not json.loads(row["acceptance_criteria"]):
        raise Invalid("acceptance_criteria_required", "A ready ticket needs acceptance criteria.")
    status_reason = redact(reason) or ("paused_by_human" if action == "pause" else None)
    _revoke(tx, row, actor, action, now)
    tx.execute(
        "UPDATE tickets SET status = ?, active_session_id = NULL,"
        " revoked_session_id = COALESCE(active_session_id, revoked_session_id),"
        " claim_epoch = claim_epoch + (active_session_id IS NOT NULL), changes_requested = ?, status_reason = ?,"
        " version = version + 1, updated_ms = ? WHERE id = ?",
        (target, int(action == "request_changes"), status_reason, now, row["id"]),
    )
    events.emit(tx, "ticket.transitioned", "ticket", row["id"], actor,
                {"key": key, "from": row["status"], "to": target, "action": action}, now)
    return get(tx, key, stall_cutoff)


def assign(tx: Tx, actor: Actor, key: str, agent_id: str | None, now: int, stall_cutoff: int,
           version: int | None = None) -> dict:
    row = _row(tx, key)
    _check_version(row, version)
    if row["status"] in CLOSED:
        raise Conflict("invalid_transition", f"{key} is {row['status']}.", ticket=key)
    if agent_id and not tx.scalar("SELECT 1 FROM agents WHERE id = ? AND archived_ms IS NULL", (agent_id,)):
        raise NotFound("unknown_agent", "No such agent.")
    status = row["status"]
    holder_agent = tx.scalar("SELECT agent_id FROM sessions WHERE id = ?", (row["active_session_id"],))
    revoke = status == "in_progress" and holder_agent != agent_id
    if status == "backlog" and agent_id:
        if not json.loads(row["acceptance_criteria"]):
            raise Invalid("acceptance_criteria_required", "Add acceptance criteria before assigning a backlog ticket.")
        status = "ready"
    if revoke:
        status = "ready"
        _revoke(tx, row, actor, "reassign", now)
    tx.execute(
        "UPDATE tickets SET assignee_agent_id = ?, status = ?,"
        " active_session_id = CASE WHEN ? THEN NULL ELSE active_session_id END,"
        " revoked_session_id = CASE WHEN ? THEN active_session_id ELSE revoked_session_id END,"
        " claim_epoch = claim_epoch + ?, version = version + 1, updated_ms = ? WHERE id = ?",
        (agent_id, status, revoke, revoke, int(revoke), now, row["id"]),
    )
    events.emit(tx, "ticket.assigned", "ticket", row["id"], actor, {"key": key, "agent_id": agent_id}, now)
    return get(tx, key, stall_cutoff)


EDITABLE = ("title", "description", "priority", "type", "acceptance_criteria")


def edit(tx: Tx, actor: Actor, key: str, changes: dict, now: int, stall_cutoff: int, version: int | None = None) -> dict:
    row = _row(tx, key)
    _check_version(row, version)
    updates = {k: redact(v) if isinstance(v, str) else v for k, v in changes.items() if k in EDITABLE and v is not None}
    if "title" in updates and not str(updates["title"]).strip():
        raise Invalid("title_required", "A ticket needs a title.")
    if "acceptance_criteria" in updates:
        criteria = _criteria(updates["acceptance_criteria"])
        if row["status"] == "ready" and not criteria:
            raise Invalid("acceptance_criteria_required", "A ready ticket needs acceptance criteria.")
        updates["acceptance_criteria"] = json.dumps(criteria)
    if updates:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        tx.execute(f"UPDATE tickets SET {assignments}, version = version + 1, updated_ms = ? WHERE id = ?",
                   (*updates.values(), now, row["id"]))
        events.emit(tx, "ticket.edited", "ticket", row["id"], actor, {"key": key, "fields": sorted(updates)}, now)
    return get(tx, key, stall_cutoff)


def pending_notices(tx: Tx, session, cursor: int, include_pending: bool) -> tuple[list[dict], list[dict]]:
    """STOP notices since the cursor, and assignments: all pending ones, or only those new since the cursor."""
    revoked = tx.all(
        "SELECT json_extract(payload, '$.key') AS key, json_extract(payload, '$.reason') AS reason FROM events"
        " WHERE id > ? AND type = 'ticket.revoked' AND json_extract(payload, '$.session_id') = ? ORDER BY id",
        (cursor, session["id"]),
    )
    if include_pending:
        assigned = tx.all("SELECT key, title FROM tickets WHERE status = 'ready' AND assignee_agent_id = ?"
                          " ORDER BY priority, seq", (session["agent_id"],))
    else:
        assigned = tx.all(
            "SELECT DISTINCT t.key, t.title FROM events e JOIN tickets t ON t.id = e.entity_id"
            " WHERE e.id > ? AND e.type = 'ticket.assigned' AND json_extract(e.payload, '$.agent_id') = ?"
            " AND t.status = 'ready' AND t.assignee_agent_id = ?", (cursor, session["agent_id"], session["agent_id"]))
    return ([{"ticket": r["key"], "reason": r["reason"]} for r in revoked],
            [{"ticket": r["key"], "title": r["title"]} for r in assigned])

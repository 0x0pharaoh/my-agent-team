"""Pure translation functions between Hermes kanban events and my-team ops.

Hermes -> my-team direction:
  kanban_task_claimed  -> session_register + ticket_claim (409 -> block)
  kanban_task_completed -> ticket_update review (never call ticket_transition done)
  kanban_task_blocked   -> ticket_update block with a note
  Task created in Hermes -> ticket_create (arrives as "proposed")

my-team -> Hermes direction (pulled on on_session_start + dispatch tick via events_since):
  ready tickets assigned to a Hermes seat -> kanban_task_create
  ticket.revoked / ticket.transitioned(done) -> update matching Hermes task
"""
from __future__ import annotations

import hashlib
from typing import Any


def ticket_hash(key: str, status: str, assignee: str | None) -> str:
    """Deterministic hash of a ticket's mutable fields for echo-loop detection."""
    h = hashlib.sha256()
    h.update(key.encode())
    h.update(status.encode())
    h.update((assignee or "").encode())
    return h.hexdigest()[:16]


def hermes_task_to_myteam_payload(
    task_id: str, title: str, body: str, assignee: str | None, status: str, session_id: str
) -> dict[str, Any]:
    """Translate a Hermes kanban task into a my-team ticket_create / ticket_update payload."""
    return {
        "title": title,
        "body": body,
        "status": _map_hermes_status_to_myteam(status),
        "assignee": assignee,
    }


def _map_hermes_status_to_myteam(status: str) -> str:
    table = {
        "open": "proposed",
        "in_progress": "in_progress",
        "done": "done",
        "blocked": "blocked",
        "cancelled": "cancelled",
    }
    return table.get(status, "proposed")


def myteam_event_to_hermes_action(
    event: dict[str, Any], mapping: dict[str, str], seat_agent_id: str | None
) -> dict[str, Any] | None:
    """Given a my-team event and the current ticket->task mapping, decide what
    Hermes kanban action (if any) to take. Returns None when no action is needed.

    Seat_agent_id: the agent_id of the Hermes seat owning this plugin (from session_register).

    Real my-team event schema (from ops.py / daemon event emission):
      {id, type, entity_type, entity_id, actor_type, actor_id, payload, created_ms}

    Relevant ticket event types and their payload shapes:
      ticket.created     {key, title, status}
      ticket.assigned    {key, agent_id}
      ticket.transitioned {key, from, to, action}
      ticket.revoked     {key, session_id, reason}
      ticket.claimed     {key, epoch}
    There are NO ticket.done, ticket.cancel, or ticket.updated events, and no "data" wrapper.
    """
    etype = event.get("type", "")
    payload = event.get("payload", {})

    # --- my-team -> Hermes ---

    if etype == "ticket.assigned":
        ticket_key = payload.get("key")
        new_agent_id = payload.get("agent_id")
        task_id = mapping.get(ticket_key)
        if task_id and new_agent_id == seat_agent_id:
            return {
                "action": "create_task",
                "task_id": task_id,
                "title": ticket_key,
                "assignee": seat_agent_id,
                "initial_status": "ready",
                "body": "",
            }
        if task_id and new_agent_id != seat_agent_id:
            return {
                "action": "block_task",
                "task_id": task_id,
                "reason": f"reassigned to {new_agent_id} in my-team",
            }
        return None

    if etype == "ticket.revoked":
        ticket_key = payload.get("key")
        task_id = mapping.get(ticket_key)
        if task_id:
            return {
                "action": "block_task",
                "task_id": task_id,
                "reason": "ticket revoked in my-team",
            }
        return None

    if etype == "ticket.transitioned":
        ticket_key = payload.get("key")
        to_status = payload.get("to", "")
        task_id = mapping.get(ticket_key)
        if task_id and to_status == "done":
            return {
                "action": "complete_task",
                "task_id": task_id,
                "summary": "",
            }
        if task_id and to_status == "blocked":
            return {
                "action": "block_task",
                "task_id": task_id,
                "reason": "transitioned to blocked in my-team",
            }
        return None

    if etype == "ticket.claimed":
        # Hermes already owns the claim; no action needed in kanban
        return None

    return None


def myteam_events_to_hermes_actions(
    events: list[dict[str, Any]], mapping: dict[str, str], seat_agent_id: str | None
) -> list[dict[str, Any]]:
    """Run myteam_event_to_hermes_action over a list of events, deduplicating by
    (action, task_id) so a burst of events on the same ticket only produces one action."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for event in events:
        action = myteam_event_to_hermes_action(event, mapping, seat_agent_id)
        if action is None:
            continue
        key = (action["action"], action.get("task_id", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out

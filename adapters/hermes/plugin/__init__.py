"""Hermes plugin: my-team kanban bridge.

Registers hooks:

  • on_session_start        — pull my-team events via events_since, reconcile projected tasks
  • on_kanban_dispatch_tick — same pull; detect newly-created Hermes tasks → project as proposed
  • on_kanban_task_claimed  — session_register + ticket_claim (409 → block)
  • on_kanban_task_completed — ticket_update review (never ticket_transition done)
  • on_kanban_task_blocked   — ticket_update block with a note

All my-team access goes through ``<cli_path()> call …`` subprocesses. The CLI path is
resolved from HERMES_MY_TEAM_CLI env var, then shutil.which("my-team"), then "my-team".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .bridge import (
    myteam_events_to_hermes_actions,
)

PLUGIN_STORAGE_KEY = "my-team-kanban-bridge"


def cli_path() -> str:
    return os.environ.get("HERMES_MY_TEAM_CLI") or shutil.which("my-team") or "my-team"


def _call(op: str, payload: dict | None, project: str | None, session: str | None) -> dict:
    args = [op]
    if payload is not None:
        args.append(json.dumps(payload))
    proc = subprocess.run(
        [cli_path(), "call", *args],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **({} if project is None else {"MY_TEAM_PROJECT": project})},
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"my-team call {op!r} failed: {proc.stderr}")
    return json.loads(proc.stdout)


def _read_state(ctx) -> dict:
    raw = ctx.state.get(PLUGIN_STORAGE_KEY) if hasattr(ctx, "state") else None
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def _write_state(ctx, state: dict) -> None:
    if hasattr(ctx, "state"):
        ctx.state.set(PLUGIN_STORAGE_KEY, json.dumps(state, separators=(",", ":")))


def register(ctx):
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("on_kanban_dispatch_tick", on_kanban_dispatch_tick)
    ctx.register_hook("on_kanban_task_claimed", on_kanban_task_claimed)
    ctx.register_hook("on_kanban_task_completed", on_kanban_task_completed)
    ctx.register_hook("on_kanban_task_blocked", on_kanban_task_blocked)


# ---------------------------------------------------------------------------
# Hook implementations
# ---------------------------------------------------------------------------

def on_session_start(event, ctx, **kwargs):
    state = _read_state(ctx)
    seat_agent_id = _seat_agent_id(event, state)
    if not _project_and_session(state):
        return
    _reconcile(state, seat_agent_id, ctx)
    _write_state(ctx, state)


def on_kanban_dispatch_tick(event, ctx, **kwargs):
    state = _read_state(ctx)
    seat_agent_id = _seat_agent_id(event, state)
    if not _project_and_session(state):
        return
    _reconcile(state, seat_agent_id, ctx)
    _write_state(ctx, state)


def on_kanban_task_claimed(event, ctx, **kwargs):
    state = _read_state(ctx)
    seat_agent_id = _seat_agent_id(event, state)
    _handle_claimed(event, state, seat_agent_id, ctx)
    _write_state(ctx, state)


def on_kanban_task_completed(event, ctx, **kwargs):
    state = _read_state(ctx)
    seat_agent_id = _seat_agent_id(event, state)
    _handle_completed(event, state, seat_agent_id, ctx)
    _write_state(ctx, state)


def on_kanban_task_blocked(event, ctx, **kwargs):
    state = _read_state(ctx)
    seat_agent_id = _seat_agent_id(event, state)
    _handle_blocked(event, state, seat_agent_id, ctx)
    _write_state(ctx, state)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _project_and_session(state: dict) -> tuple[str, str] | None:
    project = state.get("project")
    session = state.get("session")
    if not project or not session:
        return None
    return project, session


def _seat_agent_id(event, state: dict) -> str | None:
    aid = getattr(event, "agent_id", None)
    if aid:
        return aid
    if state.get("_seat_agent_id"):
        return state["_seat_agent_id"]
    return os.environ.get("HERMES_SEAT_AGENT_ID")


def _reconcile(state: dict, seat_agent_id: str | None, ctx) -> None:
    cursor = state.get("cursor", 0)
    pm = _project_and_session(state)
    if pm is None:
        return
    project, session = pm
    try:
        events_data = call_myteam("events_since", {"after": cursor, "limit": 100}, project, session, ctx)
    except RuntimeError:
        return
    event_list: list[dict] = events_data.get("events", [])
    if not event_list:
        return
    mapping = state.get("mapping", {})
    actions = myteam_events_to_hermes_actions(event_list, mapping, seat_agent_id)
    state["pending_actions"] = actions
    last_id = max((e.get("id", 0) for e in event_list), default=cursor)
    state["cursor"] = last_id


def _handle_claimed(event, state: dict, seat_agent_id: str | None, ctx) -> None:
    task_id = getattr(event, "task_id", None) or event.get("task_id")
    if not task_id:
        return
    pm = _project_and_session(state)
    if pm is None:
        return
    project, session = pm
    mapping = state.get("mapping", {})
    ticket_key = _task_to_ticket_key(mapping, task_id)
    if not ticket_key or ticket_key.startswith("_"):
        return
    if not state.get("session"):
        try:
            reg = call_myteam(
                "session_register",
                {
                    "agent_type": "hermes",
                    "native_id": seat_agent_id or "",
                    "root_path": str(Path.cwd()),
                },
                project,
                None,
                ctx,
            )
            session = reg.get("session_id")
            if session:
                state["session"] = session
        except RuntimeError:
            return
    try:
        call_myteam(
            "ticket_claim",
            {"key": ticket_key, "agent_id": seat_agent_id or ""},
            project,
            state["session"],
            ctx,
        )
    except RuntimeError as exc:
        if "409" in str(exc):
            _record_block(state, task_id, "claimed elsewhere in my-team")


def _handle_completed(event, state: dict, seat_agent_id: str | None, ctx) -> None:
    task_id = getattr(event, "task_id", None) or event.get("task_id")
    if not task_id:
        return
    pm = _project_and_session(state)
    if pm is None:
        return
    project, session = pm
    mapping = state.get("mapping", {})
    ticket_key = _task_to_ticket_key(mapping, task_id)
    if not ticket_key or ticket_key.startswith("_"):
        return
    summary = getattr(event, "summary", "") or ""
    call_myteam(
        "ticket_update",
        {"key": ticket_key, "action": "review", "summary": summary},
        project,
        session,
        ctx,
    )


def _handle_blocked(event, state: dict, seat_agent_id: str | None, ctx) -> None:
    task_id = getattr(event, "task_id", None) or event.get("task_id")
    if not task_id:
        return
    pm = _project_and_session(state)
    if pm is None:
        return
    project, session = pm
    mapping = state.get("mapping", {})
    ticket_key = _task_to_ticket_key(mapping, task_id)
    if not ticket_key or ticket_key.startswith("_"):
        return
    reason = getattr(event, "reason", "") or "blocked in Hermes"
    call_myteam(
        "ticket_update",
        {"key": ticket_key, "action": "block", "note": reason},
        project,
        session,
        ctx,
    )


def _task_to_ticket_key(mapping: dict, task_id: str) -> str | None:
    for tk, tid in mapping.items():
        if tid == task_id and isinstance(tk, str) and not tk.startswith("_"):
            return tk
    return None


def _record_block(state: dict, task_id: str, reason: str) -> None:
    pending = state.get("pending_blocks", [])
    pending.append({"task_id": task_id, "reason": reason})
    state["pending_blocks"] = pending


def call_myteam(op: str, payload: dict | None, project: str, session: str, ctx) -> dict:
    """Call `my-team call <op>` as a subprocess and return parsed JSON stdout.

    Injected for testability: the real implementation shells out; tests override this.
    """
    args = [op]
    if payload is not None:
        args.append(json.dumps(payload))
    proc = subprocess.run(
        [cli_path(), "call", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"my-team call {op!r} failed: {proc.stderr}")
    return json.loads(proc.stdout)

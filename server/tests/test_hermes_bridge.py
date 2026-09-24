"""Tests for the Hermes ↔ my-team bridge translation layer.

The bridge logic is kept in pure functions inside ``adapters/hermes/plugin/bridge.py``
so it can be unit-tested without a running daemon or a Hermes installation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.hermes.plugin.bridge import (
    build_mapping_update,
    myteam_event_to_hermes_action,
    myteam_events_to_hermes_actions,
    ticket_hash,
)

# ---------------------------------------------------------------------------
# ticket_hash
# ---------------------------------------------------------------------------

class TestTicketHash:
    def test_deterministic(self) -> None:
        h1 = ticket_hash("ready", "hermes-seat-1", "MT-7", "Implement login")
        h2 = ticket_hash("ready", "hermes-seat-1", "MT-7", "Implement login")
        assert h1 == h2

    def test_changes_on_status(self) -> None:
        assert ticket_hash("ready", "", "X", "Y") != ticket_hash("done", "", "X", "Y")

    def test_changes_on_assignee(self) -> None:
        assert ticket_hash("ready", "seat-a", "X", "Y") != ticket_hash("ready", "seat-b", "X", "Y")

    def test_changes_on_body(self) -> None:
        assert ticket_hash("ready", "", "X", "old body") != ticket_hash("ready", "", "X", "new body")

    def test_none_assignee_is_empty_string(self) -> None:
        assert ticket_hash("ready", None, "X", "Y") == ticket_hash("ready", "", "X", "Y")


# ---------------------------------------------------------------------------
# myteam_event_to_hermes_action
# ---------------------------------------------------------------------------

@pytest.fixture
def seat_agent_id() -> str:
    return "agent-hermes-1"


@pytest.fixture
def mapping() -> dict[str, str]:
    return {"MT-7": "task-abc", "MT-8": "task-xyz"}


class TestMyteamEventToHermesAction:
    def test_ready_ticket_assigned_to_our_seat_creates_task(
        self, seat_agent_id: str, mapping: dict
    ) -> None:
        event = {
            "type": "ticket.assigned",
            "data": {"key": "MT-7", "assignee": seat_agent_id, "status": "ready", "body": "do it"},
        }
        action = myteam_event_to_hermes_action(event, mapping, seat_agent_id)
        assert action is not None
        assert action["action"] == "create_task"
        assert action["task_id"] == "task-abc"
        assert action["title"] == "MT-7"
        assert action["assignee"] == seat_agent_id
        assert action["initial_status"] == "ready"

    def test_ticket_assigned_to_other_seat_blocks_our_task(
        self, seat_agent_id: str, mapping: dict
    ) -> None:
        event = {
            "type": "ticket.assigned",
            "data": {"key": "MT-7", "assignee": "other-seat", "status": "ready"},
        }
        action = myteam_event_to_hermes_action(event, mapping, seat_agent_id)
        assert action is not None
        assert action["action"] == "block_task"
        assert action["task_id"] == "task-abc"
        assert action["reason"] == "reassigned to other-seat in my-team"

    def test_ticket_revoked_blocks_task(self, seat_agent_id: str, mapping: dict) -> None:
        event = {"type": "ticket.revoked", "data": {"key": "MT-7"}}
        action = myteam_event_to_hermes_action(event, mapping, seat_agent_id)
        assert action is not None
        assert action["action"] == "block_task"
        assert action["task_id"] == "task-abc"

    def test_ticket_cancel_blocks_task(self, seat_agent_id: str, mapping: dict) -> None:
        event = {"type": "ticket.cancel", "data": {"key": "MT-7"}}
        action = myteam_event_to_hermes_action(event, mapping, seat_agent_id)
        assert action is not None
        assert action["action"] == "block_task"
        assert action["task_id"] == "task-abc"

    def test_ticket_done_completes_task(self, seat_agent_id: str, mapping: dict) -> None:
        event = {"type": "ticket.done", "data": {"key": "MT-7", "summary": "ship it"}}
        action = myteam_event_to_hermes_action(event, mapping, seat_agent_id)
        assert action is not None
        assert action["action"] == "complete_task"
        assert action["task_id"] == "task-abc"
        assert action["summary"] == "ship it"

    def test_ticket_updated_reassigned_blocks_task(self, seat_agent_id: str, mapping: dict) -> None:
        event = {"type": "ticket.updated", "data": {"key": "MT-7", "assignee": "other-seat"}}
        action = myteam_event_to_hermes_action(event, mapping, seat_agent_id)
        assert action is not None
        assert action["action"] == "block_task"

    def test_unknown_event_type_returns_none(self, seat_agent_id: str, mapping: dict) -> None:
        event = {"type": "ticket.created", "data": {"key": "MT-99"}}
        assert myteam_event_to_hermes_action(event, mapping, seat_agent_id) is None

    def test_ticket_with_no_mapping_returns_none(self, seat_agent_id: str) -> None:
        event = {"type": "ticket.assigned", "data": {"key": "MT-99", "assignee": seat_agent_id, "status": "ready"}}
        assert myteam_event_to_hermes_action(event, {}, seat_agent_id) is None

    def test_empty_mapping_returns_none(self, seat_agent_id: str) -> None:
        event = {"type": "ticket.assigned", "data": {"key": "MT-7", "assignee": seat_agent_id, "status": "ready"}}
        assert myteam_event_to_hermes_action(event, {}, seat_agent_id) is None


# ---------------------------------------------------------------------------
# myteam_events_to_hermes_actions (dedup)
# ---------------------------------------------------------------------------

class TestMyteamEventsToHermesActions:
    def test_deduplicates_same_action_on_same_task(self, seat_agent_id: str, mapping: dict) -> None:
        events = [
            {"type": "ticket.assigned", "data": {"key": "MT-7", "assignee": seat_agent_id, "status": "ready"}},
            {"type": "ticket.assigned", "data": {"key": "MT-7", "assignee": seat_agent_id, "status": "ready"}},
        ]
        actions = myteam_events_to_hermes_actions(events, mapping, seat_agent_id)
        assert len(actions) == 1

    def test_different_actions_on_same_task_both_pass(self, seat_agent_id: str, mapping: dict) -> None:
        events = [
            {"type": "ticket.assigned", "data": {"key": "MT-7", "assignee": seat_agent_id, "status": "ready"}},
            {"type": "ticket.done", "data": {"key": "MT-7"}},
        ]
        actions = myteam_events_to_hermes_actions(events, mapping, seat_agent_id)
        assert len(actions) == 2
        assert {a["action"] for a in actions} == {"create_task", "complete_task"}

    def test_different_tasks_both_pass(self, seat_agent_id: str, mapping: dict) -> None:
        events = [
            {"type": "ticket.assigned", "data": {"key": "MT-7", "assignee": seat_agent_id, "status": "ready"}},
            {"type": "ticket.assigned", "data": {"key": "MT-8", "assignee": seat_agent_id, "status": "ready"}},
        ]
        actions = myteam_events_to_hermes_actions(events, mapping, seat_agent_id)
        assert len(actions) == 2

    def test_empty_list_returns_empty(self, seat_agent_id: str) -> None:
        assert myteam_events_to_hermes_actions([], {}, seat_agent_id) == []

    def test_none_seat_agent_id_still_produces_actions(self) -> None:
        events = [
            {"type": "ticket.revoked", "data": {"key": "MT-7"}},
        ]
        mapping = {"MT-7": "task-abc"}
        actions = myteam_events_to_hermes_actions(events, mapping, None)
        assert len(actions) == 1
        assert actions[0]["action"] == "block_task"


# ---------------------------------------------------------------------------
# build_mapping_update
# ---------------------------------------------------------------------------

class TestBuildMappingUpdate:
    def test_basic(self) -> None:
        upd = build_mapping_update("MT-7", "task-abc", "in_progress", "agent-hermes-1")
        assert upd["myteam_ticket_key"] == "MT-7"
        assert upd["hermes_task_id"] == "task-abc"
        assert upd["status"] == "in_progress"
        assert upd["assignee"] == "agent-hermes-1"

    def test_none_assignee_stored_as_none(self) -> None:
        upd = build_mapping_update("MT-7", "task-abc", "open", None)
        assert upd["assignee"] is None

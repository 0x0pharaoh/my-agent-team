"""Bridge translation tests. Every event below comes from real ops via conftest fixtures."""
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

_bridge_path = Path(__file__).resolve().parents[2] / "adapters" / "hermes" / "plugin" / "bridge.py"
_spec = importlib.util.spec_from_file_location("bridge", _bridge_path)
_bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bridge)

ticket_hash = _bridge.ticket_hash
myteam_events_to_hermes_actions = _bridge.myteam_events_to_hermes_actions
myteam_event_to_hermes_action = _bridge.myteam_event_to_hermes_action


def _load_plugin():
    import sys
    plugin_dir = Path(__file__).resolve().parents[2] / "adapters" / "hermes" / "plugin"
    assert (plugin_dir / "plugin.yaml").is_file()
    for name, path in (("hplugin.bridge", plugin_dir / "bridge.py"),
                       ("hplugin", plugin_dir / "__init__.py")):
        spec = importlib.util.spec_from_file_location(
            name, path, submodule_search_locations=[str(plugin_dir)] if name == "hplugin" else None)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules["hplugin"]


class _Store:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class _Ctx:
    def __init__(self):
        self.state = _Store()
        self.hooks = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


class TestPluginRegistration:
    def test_registers_only_hooks_hermes_fires(self) -> None:
        plugin = _load_plugin()
        ctx = _Ctx()
        plugin.register(ctx)
        assert set(ctx.hooks) == {"on_session_start", "kanban_task_claimed", "kanban_task_completed",
                                  "kanban_task_blocked", "on_kanban_dispatch_tick"}

    def test_handlers_are_fail_open_without_state(self) -> None:
        plugin = _load_plugin()
        ctx = _Ctx()
        plugin.register(ctx)
        ctx.hooks["on_session_start"]()
        ctx.hooks["kanban_task_claimed"](task_id="t-1", board="b", profile_name="p")
        ctx.hooks["kanban_task_completed"](task_id="t-1", summary="s")
        ctx.hooks["kanban_task_blocked"](task_id="t-1", reason="r")
        ctx.hooks["on_kanban_dispatch_tick"](board="b", profile_name="p")
        assert ctx.state.data == {}


class TestTicketHash:
    def test_deterministic(self) -> None:
        assert ticket_hash("MT-7", "ready", "seat-1") == ticket_hash("MT-7", "ready", "seat-1")

    def test_changes_on_status(self) -> None:
        assert ticket_hash("MT-7", "ready", "") != ticket_hash("MT-7", "done", "")

    def test_changes_on_assignee(self) -> None:
        assert ticket_hash("MT-7", "ready", "seat-a") != ticket_hash("MT-7", "ready", "seat-b")

    def test_none_assignee_is_empty_string(self) -> None:
        assert ticket_hash("MT-7", "ready", None) == ticket_hash("MT-7", "ready", "")


async def _story(agent, human):
    """Drive two tickets through real ops; return events plus the keys, seat, and mapping."""
    key1 = (await human.op("ticket_create", {"title": "Bridge one", "status": "ready",
                                             "acceptance_criteria": ["x"]}))["data"]["ticket"]["key"]
    key2 = (await human.op("ticket_create", {"title": "Bridge two", "status": "ready",
                                             "acceptance_criteria": ["x"]}))["data"]["ticket"]["key"]
    seat = (await human.op("board"))["data"]["agents"][0]["id"]
    await human.op("ticket_assign", {"key": key1, "agent_id": seat})
    epoch = (await agent.op("ticket_claim", {"key": key1}))["data"]["ticket"]["claim_epoch"]
    await agent.op("ticket_update", {"key": key1, "action": "review", "epoch": epoch, "summary": "done"})
    await human.op("ticket_transition", {"key": key1, "action": "done"})
    await human.op("ticket_assign", {"key": key2, "agent_id": seat})
    await agent.op("ticket_claim", {"key": key2})
    await human.op("ticket_transition", {"key": key2, "action": "pause"})
    events = (await agent.op("events_since", {"after": 0, "limit": 200}))["data"]["events"]
    assert any(e["type"] == "ticket.assigned" for e in events), events
    return {"events": events, "mapping": {key1: "task-1", key2: "task-2"}, "seat": seat}


async def test_story_maps_to_expected_actions(agent, human):
    story = await _story(agent, human)
    actions = myteam_events_to_hermes_actions(story["events"], story["mapping"], story["seat"])
    assert [(a["action"], a["task_id"]) for a in actions] == [
        ("create_task", "task-1"), ("complete_task", "task-1"),
        ("create_task", "task-2"), ("block_task", "task-2"),
    ]


async def test_empty_mapping_yields_no_actions(agent, human):
    story = await _story(agent, human)
    assert myteam_events_to_hermes_actions(story["events"], {}, story["seat"]) == []


async def test_unmapped_types_yield_no_actions(agent, human):
    story = await _story(agent, human)
    for event in story["events"]:
        if event["type"] in ("ticket.created", "ticket.claimed", "ticket.note"):
            assert myteam_event_to_hermes_action(event, story["mapping"], story["seat"]) is None


async def test_revoked_blocks_without_a_seat(agent, human):
    story = await _story(agent, human)
    revoked = next(e for e in story["events"] if e["type"] == "ticket.revoked")
    action = myteam_event_to_hermes_action(revoked, story["mapping"], None)
    assert action is not None and action["action"] == "block_task"

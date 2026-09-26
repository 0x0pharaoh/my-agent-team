import json

import pytest

from my_team import hook
from my_team.installers import checkout, skill_source

pytestmark = pytest.mark.anyio


def test_output_matches_each_agents_hook_protocol():
    claude = json.loads(hook.output("claude-code", "prompt", "ctx"))
    assert claude["hookSpecificOutput"] == {"hookEventName": "UserPromptSubmit", "additionalContext": "ctx"}
    assert json.loads(hook.output("hermes", "prompt", "ctx")) == {"context": "ctx"}
    assert hook.output("codex", "session-start", "ctx") == "ctx"
    assert hook.output("codex", "prompt", None) == ""


def test_inactive_directory_injects_nothing(tmp_path):
    assert hook.context_text("codex", "prompt", {"session_id": "t1", "cwd": str(tmp_path)}) is None


def test_skill_ships_with_the_checkout():
    assert checkout() is not None and (skill_source() / "SKILL.md").is_file()


async def test_events_since_pages_the_log(agent, human):
    await human.op("ticket_create", {"title": "one"})
    first = await agent.op("events_since", {"after": 0})
    ids = [event["id"] for event in first["data"]["events"]]
    assert ids == sorted(ids) and any(e["type"] == "ticket.created" for e in first["data"]["events"])
    later = await agent.op("events_since", {"after": ids[-1]})
    assert later["data"]["events"] == []

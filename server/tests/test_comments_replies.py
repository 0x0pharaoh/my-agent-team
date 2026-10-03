import json
import subprocess
import sys
from pathlib import Path

import pytest

from my_team import agent_clis, runner
from my_team.context import render_context
from my_team.domain import runs

pytestmark = pytest.mark.anyio
FAKE = str(Path(__file__).with_name("fake_agent.py"))


async def test_ticket_comments_from_human_and_agents(human, agent):
    ticket = (await human.op("ticket_create", {"title": "Add search", "status": "ready",
                                               "acceptance_criteria": ["works"]}))["data"]["ticket"]
    await human.op("message_send", {"to": ticket["key"], "body": "Prefer the existing index."})
    await agent.op("message_send", {"to": ticket["key"], "body": "Noted, will reuse it."})
    comments = (await human.op("ticket_history", {"key": ticket["key"]}))["data"]["comments"]
    assert [(c["from"], c["body"]) for c in comments] == [("human", "Prefer the existing index."),
                                                          ("claude-code", "Noted, will reuse it.")]
    assert not any(c["requires_response"] for c in comments)


async def test_human_messages_come_first_until_answered(human, agent):
    sent = (await human.op("message_send", {"to": "claude-code", "body": "Status of the parser?"}))["data"]
    context = (await agent.op("team_context", {"delta": True}))["data"]
    assert [m["id"] for m in context["awaiting_reply"]] == [sent["message_id"]]
    text = render_context(context, {"scope": "directory"}, full=False)
    assert text.startswith(f"The human is waiting for your reply to message {sent['message_id']}")
    await agent.op("message_send", {"to": "human", "body": "Half done.", "reply_to": sent["message_id"]})
    await agent.op("inbox", {"ack": [sent["message_id"]]})
    assert (await agent.op("team_context", {"delta": True}))["data"]["awaiting_reply"] == []
    recent = {m["id"]: m for m in (await human.op("messages_recent", {}))["data"]["messages"]}
    assert recent[sent["message_id"]]["requires_response"] and recent[sent["message_id"]]["replied"]


async def test_offline_seat_gets_one_reply_run_and_live_seats_none(state, human, agent, repo, tmp_path, monkeypatch):
    marker = tmp_path / "go"
    marker.touch()
    monkeypatch.setattr(agent_clis, "resolve", lambda agent_type: "fake-cli")
    monkeypatch.setattr(agent_clis, "detect", lambda: [{"agent_type": t, "installed": True}
                                                       for t in ("claude-code", "codex")])
    monkeypatch.setitem(runner.DRIVERS, "codex",
                        runner.Driver(lambda cli, ctx: [sys.executable, FAKE, "ok", str(marker)], runner._claude_parse))
    monkeypatch.setattr(runs, "REPLY_GRACE_MS", -1000)
    ticket = (await human.op("ticket_create", {"title": "T", "status": "ready", "acceptance_criteria": ["a"]}))
    await human.op("ticket_assign", {"key": ticket["data"]["ticket"]["key"], "agent_type": "codex"})
    await human.op("ticket_transition", {"key": ticket["data"]["ticket"]["key"], "action": "cancel"})
    await human.op("settings_update", {"auto_run": True})
    to_codex = (await human.op("message_send", {"to": "codex", "body": "Are you there?"}))["data"]
    await human.op("message_send", {"to": "claude-code", "body": "And you?"})

    runner.tick(state)
    for thread in list(runner.THREADS.values()):
        thread.join(timeout=60)
    runner.tick(state)
    replies = [r for r in (await human.op("runs_list", {}))["data"]["runs"] if r["kind"] == "reply"]
    assert [(r["agent"], r["message_id"], r["status"]) for r in replies] == [
        ("codex", to_codex["message_id"], "succeeded")]
    env = json.loads(marker.with_suffix(".env").read_text())
    assert Path(env["cwd"]).samefile(repo) and env["MY_TEAM_AGENT_TYPE"] == "codex"
    subprocess.run(["git", "status"], cwd=repo, check=True, capture_output=True)

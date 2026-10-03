import sys
from pathlib import Path

import pytest

from my_team import agent_clis, runner
from my_team.domain import runs
from tests.conftest import Agent

pytestmark = pytest.mark.anyio
FAKE = str(Path(__file__).with_name("fake_agent.py"))


async def lead_seat(human, worker_seat_name="claude-code") -> dict:
    """Creates the codex seat (no session yet) and makes it the claude-code seat's lead."""
    ticket = (await human.op("ticket_create", {"title": "seed", "status": "ready", "acceptance_criteria": ["a"]}))
    await human.op("ticket_assign", {"key": ticket["data"]["ticket"]["key"], "agent_type": "codex"})
    seats = {s["display_name"]: s for s in (await human.op("board", {}))["data"]["agents"]}
    await human.op("agent_update", {"agent_id": seats[worker_seat_name]["id"], "role": "worker",
                                    "lead_id": seats["codex"]["id"]})
    await human.op("agent_update", {"agent_id": seats["codex"]["id"], "role": "lead"})
    return seats["codex"]


async def test_questions_go_to_the_lead_and_decisions_to_the_human(human, agent, http, state, repo):
    await lead_seat(human)
    question = (await agent.op("ask", {"prompt": "Which index should search use?"}))["data"]["question"]
    decision = (await agent.op("ask_human", {"prompt": "Ship now?", "kind": "decision", "options": ["yes", "no"]}))
    assert question["routed_to"] == "codex" and decision["data"]["question"]["routed_to"] is None

    lead = await Agent(http, state, agent_type="codex", native_id="lead-1").join(repo)
    waiting = (await lead.op("team_context", {"delta": True}))["data"]["questions_for_you"]
    assert [q["id"] for q in waiting] == [question["id"]]
    assert (await agent.op("question_reply", {"id": question["id"], "answer": "mine"}))["error"]["code"] == \
        "not_routed_to_you"
    answered = (await lead.op("question_reply", {"id": question["id"], "answer": "The title index."}))["data"]
    assert answered["question"]["status"] == "answered"
    notices = (await agent.op("team_context", {"delta": True}))["data"]["notices"]
    assert [a["answer"] for a in notices["answers"]] == ["The title index."]


async def test_lead_can_escalate_to_the_human(human, agent, http, state, repo):
    await lead_seat(human)
    question = (await agent.op("ask", {"prompt": "Can we drop IE support?"}))["data"]["question"]
    lead = await Agent(http, state, agent_type="codex", native_id="lead-2").join(repo)
    escalated = (await lead.op("question_escalate", {"id": question["id"]}))["data"]["question"]
    assert escalated["routed_to"] is None and escalated["escalated_ms"]
    inbox = (await human.op("questions_list", {"status": "open"}))["data"]["questions"]
    assert any(q["id"] == question["id"] and q["routed_to"] is None for q in inbox)


async def test_offline_lead_gets_a_run_and_unanswered_questions_escalate(state, human, agent, tmp_path, monkeypatch):
    marker = tmp_path / "go"
    marker.touch()
    monkeypatch.setattr(agent_clis, "resolve", lambda agent_type: "fake-cli")
    monkeypatch.setattr(agent_clis, "detect", lambda: [{"agent_type": "codex", "installed": True}])
    monkeypatch.setitem(runner.DRIVERS, "codex",
                        runner.Driver(lambda cli, ctx: [sys.executable, FAKE, "ok", str(marker)], runner._claude_parse))
    monkeypatch.setattr(runs, "REPLY_GRACE_MS", -1000)
    await lead_seat(human)
    seed = (await human.op("ticket_find", {"status": ["ready"]}))["data"]["tickets"][0]
    await human.op("ticket_transition", {"key": seed["key"], "action": "cancel"})
    await human.op("settings_update", {"auto_run": True})
    question = (await agent.op("ask", {"prompt": "Rename the module?"}))["data"]["question"]

    runner.tick(state)
    for thread in list(runner.THREADS.values()):
        thread.join(timeout=60)
    lead_runs = [r for r in (await human.op("runs_list", {}))["data"]["runs"] if r["kind"] == "reply"]
    assert [(r["agent"], r["question_id"], r["status"]) for r in lead_runs] == [("codex", question["id"], "succeeded")]
    after = next(q for q in (await human.op("questions_list", {"status": "open"}))["data"]["questions"]
                 if q["id"] == question["id"])
    assert after["routed_to"] is None and after["escalated_ms"]

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from my_team import agent_clis, runner
from my_team.domain import runs

pytestmark = pytest.mark.anyio
FAKE = str(Path(__file__).with_name("fake_agent.py"))


@pytest.fixture
def fake(monkeypatch, repo, tmp_path):
    """Claude-type runs execute tests/fake_agent.py in the given mode; the repo gets a first commit for worktrees."""
    for args in (["config", "user.email", "t@t"], ["config", "user.name", "t"], ["commit", "--allow-empty", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    marker = tmp_path / "agent-may-exit"
    monkeypatch.setattr(agent_clis, "resolve", lambda agent_type: "fake-cli")
    monkeypatch.setattr(agent_clis, "detect", lambda: [{"agent_type": "claude-code", "installed": True}])

    def use(mode):
        monkeypatch.setitem(runner.DRIVERS, "claude-code",
                            runner.Driver(lambda cli, ctx: [sys.executable, FAKE, mode, str(marker)], runner._claude_parse))
    use.marker = marker
    return use


async def assigned(human, budget: dict | None = None) -> dict:
    ticket = (await human.op("ticket_create", {"title": "Port the parser", "status": "ready",
                                               "acceptance_criteria": ["parser ported"]}))["data"]["ticket"]
    if budget:
        await human.op("ticket_edit", {"key": ticket["key"], **budget})
    await human.op("ticket_assign", {"key": ticket["key"], "agent_type": "claude-code"})
    await human.op("settings_update", {"auto_run": True})
    return ticket


def finish(state, human) -> dict:
    runner.tick(state)
    for thread in list(runner.THREADS.values()):
        thread.join(timeout=60)
    return state.project_db(human.project_id).read_sync(lambda tx: runs.listing(tx))[0]


async def test_nothing_runs_until_auto_run_is_on(state, human, fake):
    fake("ok")
    ticket = (await human.op("ticket_create", {"title": "T", "status": "ready", "acceptance_criteria": ["a"]}))
    await human.op("ticket_assign", {"key": ticket["data"]["ticket"]["key"], "agent_type": "claude-code"})
    runner.tick(state)
    assert (await human.op("runs_list", {}))["data"]["runs"] == []


async def test_successful_run_leaves_the_ticket_in_review(state, human, agent, fake):
    fake("ok")
    ticket = await assigned(human)
    runner.tick(state)
    run = (await human.op("runs_list", {"key": ticket["key"]}))["data"]["runs"][0]
    assert run["status"] == "running" and Path(run["worktree_path"]).is_dir() and run["branch"] == f"mt/{ticket['key']}"
    for _ in range(100):
        if fake.marker.with_suffix(".env").exists():
            break
        time.sleep(0.1)
    env = json.loads(fake.marker.with_suffix(".env").read_text())
    assert env["PWD"] == run["worktree_path"] and Path(env["cwd"]).samefile(run["worktree_path"])
    assert (env["MY_TEAM_AGENT"], env["MY_TEAM_AGENT_TYPE"]) == ("claude-code", "claude-code")
    claim = (await agent.op("ticket_claim", {"key": ticket["key"]}))["data"]["ticket"]
    await agent.op("ticket_update", {"key": ticket["key"], "action": "review", "epoch": claim["claim_epoch"],
                                     "summary": "done"})
    fake.marker.touch()
    run = finish(state, human)
    assert run["status"] == "succeeded" and run["tokens"] == 150 and run["summary"] == "Implemented and verified."
    assert (await human.op("ticket_find", {"key": ticket["key"]}))["data"]["tickets"][0]["status"] == "in_review"
    log = (await human.op("run_log", {"run_id": run["id"]}))["data"]
    assert '"fake-session"' in log["text"] and log["offset"] > 0


async def test_token_budget_stops_the_run_and_asks_the_human(state, human, fake):
    fake("burn")
    ticket = await assigned(human, {"max_tokens": 2000})
    run = finish(state, human)
    assert run["status"] == "budget_exhausted" and run["tokens"] > 2000
    found = (await human.op("ticket_find", {"key": ticket["key"]}))["data"]["tickets"][0]
    assert (found["status"], found["status_reason"]) == ("blocked", "budget_exhausted")
    question = (await human.op("questions_list", {"status": "open"}))["data"]["questions"][0]
    assert question["kind"] == "decision" and runs.EXTEND in question["options"]
    await human.op("question_answer", {"id": question["id"], "answer": runs.EXTEND})
    found = (await human.op("ticket_find", {"key": ticket["key"]}))["data"]["tickets"][0]
    assert (found["status"], found["max_tokens"]) == ("ready", 4000)


async def test_time_budget_and_human_stop(state, human, fake):
    fake("hang")
    await assigned(human)
    runner.tick(state)
    db = state.project_db(human.project_id)
    db.write_sync(lambda tx: tx.execute("UPDATE runs SET max_seconds = 0"))
    run = finish(state, human)
    assert run["status"] == "budget_exhausted"
    second = await assigned(human)
    runner.tick(state)
    active = (await human.op("runs_list", {"key": second["key"]}))["data"]["runs"][0]
    stopped = (await human.op("run_stop", {"run_id": active["id"]}))["data"]["run"]
    assert stopped["id"] == active["id"]
    run = finish(state, human)
    assert run["status"] == "stopped"
    assert (await human.op("ticket_find", {"key": second["key"]}))["data"]["tickets"][0]["status"] == "blocked"


async def test_failed_run_blocks_the_ticket_and_runs_are_human_only(state, human, agent, fake):
    fake("fail")
    ticket = await assigned(human)
    run = finish(state, human)
    assert (run["status"], run["error"]) == ("failed", "exit code 3")
    found = (await human.op("ticket_find", {"key": ticket["key"]}))["data"]["tickets"][0]
    assert found["status"] == "blocked" and found["status_reason"].startswith("run failed")
    assert (await agent.op("run_start", {"key": ticket["key"]}))["error"]["code"] == "human_only"


def test_parsers_count_each_message_once_and_skip_cache_reads():
    acc = {"tokens": 0, "messages": {}, "native": None, "cost": None, "text": None}
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 900, "output_tokens": 20}
    for _ in range(3):
        runner._claude_parse({"type": "assistant", "message": {"id": "m1", "usage": usage, "content": []}}, acc)
    assert acc["tokens"] == 35
    codex = {"tokens": 0, "messages": {}, "native": None, "cost": None, "text": None}
    runner._codex_parse({"type": "thread.started", "thread_id": "th-1"}, codex)
    for _ in range(2):
        runner._codex_parse({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                                                 "output_tokens": 10}}, codex)
    assert (codex["native"], codex["tokens"]) == ("th-1", 140)


def test_claude_runs_may_call_my_team_tools():
    argv = runner._claude_argv("claude", {"prompt": "p", "native": "n", "mcp_config": "m.json"})
    assert argv[argv.index("--allowedTools") + 1] == "mcp__my-team"

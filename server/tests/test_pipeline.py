import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from my_team import agent_clis, runner
from my_team.domain import runs

pytestmark = pytest.mark.anyio
FAKE = str(Path(__file__).with_name("fake_agent.py"))


class Launches(list):
    mode: dict


@pytest.fixture
def launched(monkeypatch, repo, tmp_path):
    """Fake Claude-type runs; returns the list of launch contexts so tests can read prompts and resume ids."""
    for args in (["config", "user.email", "t@t"], ["config", "user.name", "t"], ["commit", "--allow-empty", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    monkeypatch.setattr(agent_clis, "resolve", lambda agent_type: "fake-cli")
    monkeypatch.setattr(agent_clis, "detect", lambda: [{"agent_type": "claude-code", "installed": True}])
    contexts, mode = Launches(), {"value": "ok", "marker": tmp_path / "go"}

    def argv(cli, ctx):
        contexts.append(ctx)
        return [sys.executable, FAKE, mode["value"], str(mode["marker"])]
    monkeypatch.setitem(runner.DRIVERS, "claude-code", runner.Driver(argv, runner._claude_parse))
    contexts.mode = mode
    return contexts


def drain(state) -> None:
    for thread in list(runner.THREADS.values()):
        thread.join(timeout=60)


async def review(agent, key: str, summary: str) -> None:
    claim = (await agent.op("ticket_claim", {"key": key}))["data"]["ticket"]
    await agent.op("ticket_update", {"key": key, "action": "review", "epoch": claim["claim_epoch"], "summary": summary})


async def test_two_step_pipeline_hands_over_with_the_previous_summary(state, human, agent, launched):
    ticket = (await human.op("ticket_create", {"title": "Add search", "status": "ready",
                                               "acceptance_criteria": ["search works"]}))["data"]["ticket"]
    steps = [{"name": "plan", "agent_type": "claude-code"}, {"name": "implement", "agent_type": "claude-code",
                                                             "max_tokens": 5000}]
    set_up = (await human.op("ticket_workflow", {"key": ticket["key"], "steps": steps}))["data"]["ticket"]
    assert set_up["assignee"]["name"] == "claude-code" and set_up["step"] == 0
    await human.op("settings_update", {"auto_run": True})

    runner.tick(state)
    assert "step 1 of 2 (plan)" in launched[0]["prompt"]
    await review(agent, ticket["key"], "Plan: index titles first.")
    launched.mode["marker"].touch()
    drain(state)
    middle = (await human.op("ticket_find", {"key": ticket["key"]}))["data"]["tickets"][0]
    assert (middle["status"], middle["step"]) == ("ready", 1)

    launched.mode["marker"].unlink()
    runner.tick(state)
    assert "step 2 of 2 (implement)" in launched[1]["prompt"] and "Plan: index titles first." in launched[1]["prompt"]
    await review(agent, ticket["key"], "Implemented search.")
    launched.mode["marker"].touch()
    drain(state)
    final = (await human.op("ticket_find", {"key": ticket["key"]}))["data"]["tickets"][0]
    assert final["status"] == "in_review"
    history = (await human.op("runs_list", {"key": ticket["key"]}))["data"]["runs"]
    assert [(r["step_index"], r["status"], r["max_tokens"]) for r in history] == [
        (1, "succeeded", 5000), (0, "succeeded", 300_000)]


async def test_extended_budget_resumes_the_same_agent_session(state, human, launched):
    launched.mode["value"] = "burn"
    ticket = (await human.op("ticket_create", {"title": "Big job", "status": "ready",
                                               "acceptance_criteria": ["done"]}))["data"]["ticket"]
    await human.op("ticket_edit", {"key": ticket["key"], "max_tokens": 1500})
    await human.op("ticket_assign", {"key": ticket["key"], "agent_type": "claude-code"})
    await human.op("settings_update", {"auto_run": True})
    runner.tick(state)
    drain(state)
    question = (await human.op("questions_list", {"status": "open"}))["data"]["questions"][0]
    await human.op("question_answer", {"id": question["id"], "answer": runs.EXTEND})
    runner.tick(state)
    assert launched[1]["resume"] == "fake-session" and "extended it" in launched[1]["prompt"]
    argv = runner._claude_argv("claude", launched[1])
    assert argv[argv.index("--resume") + 1] == "fake-session" and "--session-id" not in argv
    runner.shutdown()
    drain(state)


def test_opencode_tokens_come_from_its_database(tmp_path, monkeypatch):
    db = tmp_path / "opencode.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, data TEXT)")
        for n, tokens in enumerate(({"input": 100, "output": 20, "reasoning": 5, "cache": {"read": 900, "write": 10}},
                                    {"input": 50, "output": 5})):
            conn.execute("INSERT INTO session_message VALUES (?, 'ses_1', ?)",
                         (str(n), json.dumps({"tokens": tokens, "cost": 0.01})))
        conn.commit()
    monkeypatch.setenv("OPENCODE_DB", str(db))
    acc = {"tokens": 0, "native": None, "cost": None, "text": None}
    runner._opencode_parse({"type": "step_start", "sessionID": "ses_1", "part": {}}, acc)
    runner._opencode_parse({"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "ok"}}, acc)
    runner._opencode_poll(acc)
    assert (acc["native"], acc["tokens"], acc["text"]) == ("ses_1", 190, "ok")


def test_hermes_usage_file_after_the_run(tmp_path):
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps({"input_tokens": 300, "output_tokens": 40, "cache_read_tokens": 5000,
                                 "cache_write_tokens": 60, "reasoning_tokens": None, "estimated_cost_usd": 0.02,
                                 "session_id": "h-1"}))
    acc = {"tokens": 0, "native": None, "cost": None}
    runner._hermes_after({"usage_file": str(usage)}, acc)
    assert (acc["tokens"], acc["cost"], acc["native"]) == (400, 0.02, "h-1")
    argv = runner._hermes_argv("hermes", {"prompt": "p", "cwd": "w", "usage_file": str(usage), "resume": "h-1"})
    assert argv[:2] == ["hermes", "-z"] and argv[-2:] == ["--resume", "h-1"]


async def test_budget_hit_after_the_step_reached_review_still_hands_over(state, human, agent, launched):
    ticket = (await human.op("ticket_create", {"title": "Two steps", "status": "ready",
                                               "acceptance_criteria": ["done"]}))["data"]["ticket"]
    await human.op("ticket_workflow", {"key": ticket["key"], "steps": [{"name": "plan", "agent_type": "claude-code"},
                                                                       {"name": "build", "agent_type": "claude-code"}]})
    db = state.project_db(human.project_id)
    run = db.write_sync(lambda tx: runs.start(tx, runs.SYSTEM, ticket["key"], 1))
    await review(agent, ticket["key"], "Planned.")
    db.write_sync(lambda tx: runs.finish(tx, run["id"], 2, "budget_exhausted"))
    after = (await human.op("ticket_find", {"key": ticket["key"]}))["data"]["tickets"][0]
    assert (after["status"], after["step"]) == ("ready", 1)
    assert (await human.op("questions_list", {"status": "open"}))["data"]["questions"] == []


async def test_opencode_run_without_my_team_config_fails_fast(state, human, launched, monkeypatch, tmp_path):
    monkeypatch.setattr(agent_clis, "detect", lambda: [{"agent_type": "opencode", "installed": True}])
    monkeypatch.setattr(runner, "opencode_config_dir", lambda home: tmp_path / "no-opencode-config")
    ticket = (await human.op("ticket_create", {"title": "OC", "status": "ready",
                                               "acceptance_criteria": ["done"]}))["data"]["ticket"]
    await human.op("ticket_assign", {"key": ticket["key"], "agent_type": "opencode"})
    await human.op("settings_update", {"auto_run": True})
    runner.tick(state)
    run = (await human.op("runs_list", {"key": ticket["key"]}))["data"]["runs"][0]
    assert run["status"] == "failed" and "my-team install opencode" in run["error"]

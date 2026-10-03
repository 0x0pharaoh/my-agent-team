import json
import subprocess
import sys
from pathlib import Path

import pytest

from my_team import agent_clis, graphify, runner
from my_team.paths import private_data_dir

pytestmark = pytest.mark.anyio
FAKE = str(Path(__file__).with_name("fake_agent.py"))


def write_graph(repo: Path) -> None:
    (repo / "graphify-out").mkdir(exist_ok=True)
    (repo / "graphify-out" / "graph.json").write_text(json.dumps({"nodes": [{}, {}, {}], "links": [{}, {}]}))


def test_status_and_code_only_build_with_git_exclude(repo, monkeypatch):
    monkeypatch.setattr(graphify, "cli", lambda: None)
    assert graphify.status(repo) == {"installed": False, "built_ms": None, "nodes": None, "edges": None,
                                     "building": False}
    calls = []
    monkeypatch.setattr(graphify, "cli", lambda: "graphify")
    real_run = subprocess.run
    monkeypatch.setattr(graphify.subprocess, "run",
                        lambda args, **kw: calls.append(args) if args[0] == "graphify" else real_run(args, **kw))
    graphify.build(repo)
    graphify.build(repo, update=True)
    assert calls == [["graphify", "extract", str(repo), "--code-only"], ["graphify", "update", str(repo)]]
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert exclude.splitlines().count("graphify-out/") == 1
    write_graph(repo)
    found = graphify.status(repo)
    assert (found["installed"], found["nodes"], found["edges"]) == (True, 3, 2) and found["built_ms"]


async def test_runs_get_the_graph_over_mcp_and_refresh_it_first(state, human, agent, repo, tmp_path, monkeypatch):
    for args in (["config", "user.email", "t@t"], ["config", "user.name", "t"], ["commit", "--allow-empty", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    write_graph(repo)
    refreshed = []
    monkeypatch.setattr(graphify, "build", lambda root, update=False: refreshed.append((root, update)))
    monkeypatch.setattr(graphify, "python", lambda: "graph-python")
    monkeypatch.setattr(agent_clis, "resolve", lambda agent_type: "fake-cli")
    monkeypatch.setattr(agent_clis, "detect", lambda: [{"agent_type": "claude-code", "installed": True}])
    marker = tmp_path / "go"
    marker.touch()
    monkeypatch.setitem(runner.DRIVERS, "claude-code",
                        runner.Driver(lambda cli, ctx: [sys.executable, FAKE, "fail", str(marker)], runner._claude_parse))
    ticket = (await human.op("ticket_create", {"title": "T", "status": "ready", "acceptance_criteria": ["a"]}))
    await human.op("ticket_assign", {"key": ticket["data"]["ticket"]["key"], "agent_type": "claude-code"})
    await human.op("settings_update", {"auto_run": True})
    runner.tick(state)
    for thread in list(runner.THREADS.values()):
        thread.join(timeout=60)
    run = (await human.op("runs_list", {}))["data"]["runs"][0]
    config = json.loads((private_data_dir() / "runs" / f"{run['id']}.mcp.json").read_text())
    assert config["mcpServers"]["graphify"] == {"command": "graph-python", "args": [
        "-m", "graphify.serve", str(repo / "graphify-out" / "graph.json")]}
    assert refreshed == [(Path(repo), True)]


def test_codex_gets_graphify_flags_only_with_a_graph():
    base = {"cwd": "w", "prompt": "p", "my_team_cli": "mt", "mcp_env": {"MY_TEAM_AGENT": "codex"}}
    assert "mcp_servers.graphify.command='py'" not in runner._codex_argv("codex", base)
    argv = runner._codex_argv("codex", base | {"graph": {"command": "py", "args": ["-m", "graphify.serve", "g.json"]}})
    assert "mcp_servers.graphify.command='py'" in argv
    assert "mcp_servers.graphify.args=['-m', 'graphify.serve', 'g.json']" in argv


async def test_graph_output_is_free_to_edit(agent, repo):
    verdict = (await agent.op("edit_check", {"path": str(repo / "graphify-out" / "graph.json")}))["data"]
    assert verdict["decision"] == "allow"

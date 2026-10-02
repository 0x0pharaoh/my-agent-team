import pytest

from my_team import activation

pytestmark = pytest.mark.anyio


async def _register(agent, native_id, via):
    return await agent.op("session_register", {"agent_type": "opencode", "native_id": native_id,
                                               "root_path": ".", "via": via})


async def _matrix(agent):
    return (await agent.op("activation_matrix", {}))["data"]


async def test_no_evidence_means_not_installed(agent, repo):
    await agent.join(repo)
    cells = (await _matrix(agent))["cells"]
    assert {cells[s][a] for s in cells for a in cells[s]} == {"Not installed"}


async def test_hook_evidence_enforces_active_scopes(agent, repo):
    await agent.join(repo)
    await _register(agent, "n-hook", "hook")
    activation.set_scope("global", True, cwd=str(repo))
    activation.set_scope("directory", True, cwd=str(repo))
    activation.set_scope("session", True, cwd=str(repo), agent_type="opencode", native_id="n-hook")
    activation.set_scope("one-time", True, cwd=str(repo), agent_type="opencode", native_id="n-once",
                         task="t")
    cells = (await _matrix(agent))["cells"]
    assert {cells[s]["opencode"] for s in cells} == {"Enforced"}
    assert cells["global"]["codex"] == "Not installed"


async def test_mcp_only_means_on_invocation(agent, repo):
    await agent.join(repo)
    await _register(agent, "n-mcp", "mcp")
    activation.set_scope("global", True, cwd=str(repo))
    cells = (await _matrix(agent))["cells"]
    assert cells["global"]["opencode"] == "On invocation only"


async def test_stale_evidence_and_idle_scopes_are_unknown(agent, repo, state):
    await agent.join(repo)
    await _register(agent, "n-old", "hook")
    await _register(agent, "n-old-mcp", "mcp")
    state.project_db(agent.project_id).write_sync(
        lambda tx: tx.execute("UPDATE agent_evidence SET hook_seen_ms = 0, mcp_seen_ms = 0"))
    activation.set_scope("global", True, cwd=str(repo))
    assert (await _matrix(agent))["cells"]["global"]["opencode"] == "Unknown"


async def test_inactive_scope_is_unknown_despite_fresh_hooks(agent, repo):
    await agent.join(repo)
    await _register(agent, "n-hook", "hook")
    assert (await _matrix(agent))["cells"]["global"]["opencode"] == "Unknown"


async def test_matrix_visible_to_agent_and_human(agent, repo, human):
    await agent.join(repo)
    for result in (await _matrix(agent), (await human.op("activation_matrix", {}))["data"]):
        assert result["scopes"] == ["global", "directory", "session", "one-time"]
        assert result["agents"] == ["claude-code", "codex", "opencode", "hermes"]
        assert set(result["cells"]) == set(result["scopes"])

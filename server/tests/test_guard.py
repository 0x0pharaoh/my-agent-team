import pytest

from my_team.domain.guard import matches
from tests.conftest import Agent

pytestmark = pytest.mark.anyio


def test_path_patterns():
    assert matches("src/api/users.py", "src/api")
    assert matches("src/api/users.py", "src/api/")
    assert matches("src/api/users.py", "src/**/*.py")
    assert not matches("src/apiv2/x.py", "src/api")
    assert matches("README.md", "README.md")


async def claimed(agent, human, title, paths):
    ticket = (await human.op("ticket_create", {"title": title, "status": "ready",
                                               "acceptance_criteria": ["done"]}))["data"]["ticket"]
    result = await agent.op("ticket_claim", {"key": ticket["key"], "paths": paths})
    assert result["success"], result
    return result["data"]["ticket"]


async def test_editing_without_a_claim_asks(agent, repo):
    verdict = (await agent.op("edit_check", {"path": str(repo / "src" / "app.py")}))["data"]
    assert verdict["decision"] == "ask" and "no claimed ticket" in verdict["reason"]


async def test_docs_and_outside_files_are_free(agent, repo, tmp_path):
    assert (await agent.op("edit_check", {"path": str(repo / "docs" / "PRD.md")}))["data"]["decision"] == "allow"
    assert (await agent.op("edit_check", {"path": str(tmp_path / "elsewhere.py")}))["data"]["decision"] == "allow"


async def test_another_sessions_claimed_paths_ask(http, state, repo, agent, human):
    other = await Agent(http, state, native_id="native-2").join(repo)
    await claimed(other, human, "API work", ["src/api"])
    await claimed(agent, human, "UI work", ["ui/"])
    mine = (await agent.op("edit_check", {"path": str(repo / "ui" / "Board.tsx")}))["data"]
    assert mine["decision"] == "allow"
    theirs = (await agent.op("edit_check", {"path": str(repo / "src" / "api" / "users.py")}))["data"]
    assert theirs["decision"] == "ask" and "claude-code-2" in theirs["reason"]


async def test_paths_can_be_updated_while_holding(agent, human):
    ticket = await claimed(agent, human, "Grow scope", ["a/"])
    updated = await agent.op("ticket_update", {"key": ticket["key"], "action": "note",
                                               "epoch": ticket["claim_epoch"], "paths": ["a/", "b/"]})
    assert updated["data"]["ticket"]["paths"] == ["a/", "b/"]

import pytest

from my_team import activation
from tests.conftest import Agent

pytestmark = pytest.mark.anyio


async def _claim(agent, repo):
    return await agent.registry("init_claim", {"cwd": str(repo), "session_id": agent.session_id})


async def _other(http, state, repo):
    return await Agent(http, state, native_id="native-2").join(repo)


async def test_first_session_claims_and_reenters(agent, repo):
    first = await _claim(agent, repo)
    assert first["data"]["claimed"] is True
    again = await _claim(agent, repo)
    assert again["data"]["claimed"] is True
    assert again["data"]["epoch"] == first["data"]["epoch"]


async def test_second_session_loads_holder_state(http, state, repo, agent):
    await _claim(agent, repo)
    other = await _other(http, state, repo)
    held = await _claim(other, repo)
    assert held["data"]["claimed"] is False
    assert held["data"]["holder_session_id"] == agent.session_id


async def test_stale_claim_can_be_taken_over(http, state, repo, agent):
    await _claim(agent, repo)
    other = await _other(http, state, repo)
    state.registry.write_sync(lambda tx: tx.execute(
        "UPDATE init_claims SET claimed_ms = claimed_ms - 7200000 WHERE holder_session_id = ?",
        (agent.session_id,)))
    taken = await _claim(other, repo)
    assert taken["data"]["claimed"] is True
    assert taken["data"]["epoch"] == 2


async def test_human_releases_stale_claim(http, state, repo, agent, human):
    await _claim(agent, repo)
    other = await _other(http, state, repo)
    assert (await _claim(other, repo))["data"]["claimed"] is False
    released = await human.http.post("/api/v1/registry/init_release", json={"cwd": str(repo)},
                                     headers={"X-My-Team": "1"})
    assert released.json()["data"]["released"] is True
    assert (await _claim(other, repo))["data"]["claimed"] is True


async def test_agent_cannot_release(agent, repo):
    await _claim(agent, repo)
    denied = await agent.registry("init_release", {"cwd": str(repo)})
    assert denied["error"]["code"] == "human_only"


async def test_checklist_derives_from_rows(agent, repo, human):
    await agent.join(repo)
    status = await agent.op("init_status", {})
    checklist = status["data"]["checklist"]
    assert status["data"]["complete"] is False
    assert checklist["project_toml"] is True
    assert set(checklist["docs"]) == {"PRD", "ARCHITECTURE", "RULES", "DESIGN", "SECURITY"}
    assert all(not d["present"] for d in checklist["docs"].values())
    assert checklist["open_questions"] == 0 and checklist["init_tickets"] == 0
    await human.op("ticket_create", {"title": "Docs", "status": "backlog", "origin_key": "init:docs:PRD:goals",
                                     "acceptance_criteria": ["x"]})
    await agent.op("ask_human", {"prompt": "Launch date?"})
    (repo / "docs").mkdir(exist_ok=True)
    for name in ("PRD", "ARCHITECTURE", "RULES", "DESIGN", "SECURITY"):
        (repo / "docs" / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    activation.set_scope("directory", True, cwd=str(repo))
    status = await agent.op("init_status", {})
    assert status["data"]["checklist"]["open_questions"] == 1
    assert status["data"]["checklist"]["init_tickets"] == 1
    assert status["data"]["checklist"]["activation"] is True
    assert status["data"]["complete"] is True

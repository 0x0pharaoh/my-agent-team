import pytest

from my_team import agent_clis

pytestmark = pytest.mark.anyio


async def ready_ticket(human, title="Port the parser"):
    return (await human.op("ticket_create", {"title": title, "status": "ready",
                                             "acceptance_criteria": ["works"]}))["data"]["ticket"]


async def test_assign_to_an_agent_type_that_never_connected(human):
    ticket = await ready_ticket(human)
    assigned = (await human.op("ticket_assign", {"key": ticket["key"], "agent_type": "codex"}))["data"]["ticket"]
    assert assigned["assignee"]["name"] == "codex" and assigned["pending_pickup"]
    again = (await human.op("ticket_assign", {"key": (await ready_ticket(human, "Two"))["key"],
                                              "agent_type": "codex"}))["data"]["ticket"]
    assert again["assignee"]["id"] == assigned["assignee"]["id"]
    seats = (await human.op("board", {}))["data"]["agents"]
    assert [s["display_name"] for s in seats if s["agent_type"] == "codex"] == ["codex"]


async def test_settings_default_update_and_human_only(human, agent):
    board = (await human.op("board", {}))["data"]
    assert board["settings"] == {"auto_run": False, "default_max_tokens": 300_000, "default_max_minutes": 30,
                                 "max_parallel_runs": 2}
    changed = (await human.op("settings_update", {"auto_run": True, "max_parallel_runs": 3}))["data"]["settings"]
    assert changed["auto_run"] is True and changed["max_parallel_runs"] == 3 and changed["default_max_minutes"] == 30
    assert (await human.op("settings_update", {"max_parallel_runs": 99}))["error"]["code"] == "invalid_input"
    assert (await agent.op("settings_update", {"auto_run": False}))["error"]["code"] == "human_only"


async def test_roles_and_reporting_line_reject_cycles(human):
    for agent_type in ("claude-code", "codex"):
        await human.op("ticket_assign", {"key": (await ready_ticket(human, agent_type))["key"],
                                         "agent_type": agent_type})
    seats = {s["agent_type"]: s for s in (await human.op("board", {}))["data"]["agents"]}
    lead, worker = seats["claude-code"]["id"], seats["codex"]["id"]
    await human.op("agent_update", {"agent_id": lead, "role": "lead"})
    updated = (await human.op("agent_update", {"agent_id": worker, "role": "worker", "lead_id": lead}))["data"]
    row = next(s for s in updated["agents"] if s["id"] == worker)
    assert (row["role"], row["lead_id"]) == ("worker", lead)
    loop = await human.op("agent_update", {"agent_id": lead, "lead_id": worker})
    assert loop["error"]["code"] == "lead_cycle"
    assert (await human.op("agent_update", {"agent_id": worker, "role": "boss"}))["error"]["code"] == "invalid_input"
    cleared = (await human.op("agent_update", {"agent_id": worker, "lead_id": ""}))["data"]["agents"]
    assert next(s for s in cleared if s["id"] == worker)["lead_id"] is None


async def test_detect_reports_installed_clis_without_running_real_agents(human, monkeypatch, tmp_path):
    monkeypatch.setattr(agent_clis, "_cache", None)
    monkeypatch.setattr(agent_clis.shutil, "which", lambda name: str(tmp_path / name) if name == "codex" else None)
    monkeypatch.setattr(agent_clis, "_hermes_exe", lambda: None)
    monkeypatch.setattr(agent_clis, "_version", lambda path: "codex-cli 0.155.1")
    found = {a["agent_type"]: a for a in (await human.op("agents_detect", {}))["data"]["agents"]}
    assert found["codex"]["installed"] and found["codex"]["version"] == "codex-cli 0.155.1"
    assert not found["claude-code"]["installed"] and not found["hermes"]["installed"]

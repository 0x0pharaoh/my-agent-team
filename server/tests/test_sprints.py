import pytest

pytestmark = pytest.mark.anyio
DAY = 86_400_000


async def ticket(human, title, ready=True):
    body = {"title": title, "status": "ready", "acceptance_criteria": ["done"]} if ready else {"title": title}
    return (await human.op("ticket_create", body))["data"]["ticket"]


async def test_sprint_lifecycle_carries_unfinished_work_and_keeps_claims(agent, human):
    sprint = (await human.op("sprint_create", {"name": "S1"}))["data"]["sprint_id"]
    nope = await human.op("sprint_transition", {"sprint_id": sprint, "action": "start"})
    assert nope["error"]["code"] == "goal_and_end_required"
    far = 10**13
    assert (await human.op("sprint_transition", {"sprint_id": sprint, "action": "start", "goal": "Ship login",
                                                 "end_ms": far}))["success"]
    other = (await human.op("sprint_create", {"name": "S2", "goal": "g", "end_ms": far}))["data"]["sprint_id"]
    clash = await human.op("sprint_transition", {"sprint_id": other, "action": "start"})
    assert clash["error"]["code"] == "sprint_already_active"

    held, finished = await ticket(human, "Held"), await ticket(human, "Finished")
    for t in (held, finished):
        assert (await human.op("ticket_edit", {"key": t["key"], "sprint_id": sprint}))["data"]["ticket"]["sprint_id"]
    claim = (await agent.op("ticket_claim", {"key": held["key"]}))["data"]["ticket"]
    await human.op("ticket_transition", {"key": finished["key"], "action": "cancel"})

    done = await human.op("sprint_transition", {"sprint_id": sprint, "action": "complete", "move_to": other})
    s1 = next(s for s in done["data"]["sprints"] if s["id"] == sprint)
    assert s1["status"] == "completed" and s1["review_summary"] == "0 done, 1 carried over to S2."
    moved = (await human.op("ticket_find", {"key": held["key"]}))["data"]["tickets"][0]
    assert moved["sprint_id"] == other and moved["status"] == "in_progress"
    assert moved["claim_epoch"] == claim["claim_epoch"]

    closed = await human.op("ticket_edit", {"key": held["key"], "sprint_id": sprint})
    assert closed["error"]["code"] == "bad_sprint"
    back = await human.op("ticket_edit", {"key": held["key"], "sprint_id": ""})
    assert back["data"]["ticket"]["sprint_id"] is None


async def test_sprints_are_human_only(agent):
    denied = await agent.op("sprint_create", {"name": "Mine"})
    assert denied["error"]["code"] == "human_only"

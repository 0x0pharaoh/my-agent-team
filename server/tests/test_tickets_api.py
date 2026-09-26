import pytest

from tests.conftest import Agent

pytestmark = pytest.mark.anyio


async def ready_ticket(human, title="Add login", criteria=("works",)) -> dict:
    created = await human.op("ticket_create", {"title": title, "status": "ready", "acceptance_criteria": list(criteria)})
    assert created["success"], created
    return created["data"]["ticket"]


async def test_assignment_surfaces_then_claim_and_review(agent, human):
    ticket = await ready_ticket(human)
    board = await human.op("board")
    seat = board["data"]["agents"][0]
    assigned = await human.op("ticket_assign", {"key": ticket["key"], "agent_id": seat["id"]})
    assert assigned["data"]["ticket"]["pending_pickup"] is True

    context = await agent.op("team_context")
    assert [n["ticket"] for n in context["data"]["notices"]["assigned"]] == [ticket["key"]]

    claimed = await agent.op("ticket_claim", {"key": ticket["key"]})
    assert claimed["data"]["ticket"]["status"] == "in_progress"
    epoch = claimed["data"]["ticket"]["claim_epoch"]

    missing_summary = await agent.op("ticket_update", {"key": ticket["key"], "action": "review", "epoch": epoch})
    assert missing_summary["error"]["code"] == "summary_required"

    review = await agent.op("ticket_update", {"key": ticket["key"], "action": "review", "epoch": epoch,
                                              "summary": "Implemented"})
    assert review["data"]["ticket"]["status"] == "in_review"

    agent_done = await agent.op("ticket_transition", {"key": ticket["key"], "action": "done"})
    assert agent_done["error"]["code"] == "human_only"
    done = await human.op("ticket_transition", {"key": ticket["key"], "action": "done"})
    assert done["data"]["ticket"]["status"] == "done"


async def test_second_session_cannot_take_a_held_ticket(http, state, repo, agent, human):
    ticket = await ready_ticket(human)
    other = await Agent(http, state, native_id="native-2").join(repo)
    assert (await agent.op("ticket_claim", {"key": ticket["key"]}))["success"]
    blocked = await other.op("ticket_claim", {"key": ticket["key"]})
    assert blocked["error"]["code"] == "already_claimed_by"


async def test_one_active_ticket_per_session(agent, human):
    first, second = await ready_ticket(human, "one"), await ready_ticket(human, "two")
    assert (await agent.op("ticket_claim", {"key": first["key"]}))["success"]
    again = await agent.op("ticket_claim", {"key": second["key"]})
    assert again["error"]["code"] == "session_has_active_ticket"
    assert again["error"]["details"]["ticket"] == first["key"]


async def test_release_requires_a_note(agent, human):
    ticket = await ready_ticket(human)
    epoch = (await agent.op("ticket_claim", {"key": ticket["key"]}))["data"]["ticket"]["claim_epoch"]
    silent = await agent.op("ticket_update", {"key": ticket["key"], "action": "release", "epoch": epoch})
    assert silent["error"]["code"] == "note_required"
    released = await agent.op("ticket_update", {"key": ticket["key"], "action": "release", "epoch": epoch,
                                                "note": "stopped at step 2"})
    assert released["data"]["ticket"]["status"] == "ready"


async def test_human_pause_revokes_and_stops_the_agent(agent, human):
    ticket = await ready_ticket(human)
    epoch = (await agent.op("ticket_claim", {"key": ticket["key"]}))["data"]["ticket"]["claim_epoch"]
    paused = await human.op("ticket_transition", {"key": ticket["key"], "action": "pause"})
    assert paused["data"]["ticket"]["status"] == "blocked"

    context = await agent.op("team_context", {"delta": True})
    assert context["data"]["notices"]["stop"] == [{"ticket": ticket["key"], "reason": "pause"}]
    late_write = await agent.op("ticket_update", {"key": ticket["key"], "action": "note", "epoch": epoch,
                                                  "note": "still editing"})
    assert late_write["error"]["code"] == "ticket_revoked"


async def test_stale_epoch_is_rejected(agent, human):
    ticket = await ready_ticket(human)
    epoch = (await agent.op("ticket_claim", {"key": ticket["key"]}))["data"]["ticket"]["claim_epoch"]
    stale = await agent.op("ticket_update", {"key": ticket["key"], "action": "note", "epoch": epoch - 1, "note": "x"})
    assert stale["error"]["code"] == "claim_lost"


async def test_agent_tickets_start_proposed_and_need_human_acceptance(agent, human):
    proposed = await agent.op("ticket_create", {"title": "Refactor auth"})
    ticket = proposed["data"]["ticket"]
    assert ticket["status"] == "proposed"
    assert (await agent.op("ticket_claim", {"key": ticket["key"]}))["error"]["code"] == "not_claimable"
    accepted = await human.op("ticket_transition", {"key": ticket["key"], "action": "accept"})
    assert accepted["data"]["ticket"]["status"] == "backlog"


async def test_ready_requires_acceptance_criteria(human):
    bare = await human.op("ticket_create", {"title": "No AC", "status": "ready"})
    assert bare["error"]["code"] == "acceptance_criteria_required"


async def test_dependency_blocks_claim(agent, human, state):
    blocker, blocked = await ready_ticket(human, "base"), await ready_ticket(human, "on top")
    db = state.project_db(agent.project_id)
    db.write_sync(lambda tx: tx.execute("INSERT INTO ticket_links (from_id, to_id, type) VALUES (?, ?, 'blocks')",
                                        (blocker["id"], blocked["id"])))
    waiting = await agent.op("ticket_claim", {"key": blocked["key"]})
    assert waiting["error"]["code"] == "waiting_on"
    assert waiting["error"]["details"]["waiting_on"] == [blocker["key"]]


async def test_retried_create_with_client_id_is_idempotent(human):
    payload = {"id": "01M36MG6XQV8DGRQ4EGFBWY0DK", "title": "Once", "status": "backlog"}
    first, second = await human.op("ticket_create", payload), await human.op("ticket_create", payload)
    assert first["data"]["ticket"]["key"] == second["data"]["ticket"]["key"]
    reused = await human.op("ticket_create", payload | {"title": "Different"})
    assert reused["error"]["code"] == "id_reused"


async def test_version_conflict_on_stale_edit(human):
    ticket = await ready_ticket(human)
    await human.op("ticket_edit", {"key": ticket["key"], "version": ticket["version"], "title": "New"})
    stale = await human.op("ticket_edit", {"key": ticket["key"], "version": ticket["version"], "title": "Newer"})
    assert stale["error"]["code"] == "version_conflict"

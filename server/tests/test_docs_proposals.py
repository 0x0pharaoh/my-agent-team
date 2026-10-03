import hashlib

import pytest

pytestmark = pytest.mark.anyio

TABLE = "| Tool | Purpose |\n|---|---|\n| FastAPI | API |\n"


def _write_docs(repo):
    docs = repo / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "ARCHITECTURE.md").write_text("# ARCHITECTURE\n\n## Overview\nBase.\n", encoding="utf-8")
    (docs / "RULES.md").write_text("# RULES\n\n## Overview\nBase.\n", encoding="utf-8")


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_propose_intent_stays_pending(agent, repo):
    _write_docs(repo)
    await agent.join(repo)
    before = _hash(repo / "docs" / "RULES.md")
    result = await agent.op("docs_propose", {"doc": "RULES", "anchor": "Overview", "content": "New rule."})
    proposal = result["data"]["proposal"]
    assert proposal["class"] == "intent" and proposal["status"] == "pending"
    assert _hash(repo / "docs" / "RULES.md") == before


async def test_daemon_assigns_class_and_ignores_client_claim(agent, repo):
    _write_docs(repo)
    await agent.join(repo)
    result = await agent.op("docs_propose", {"doc": "ARCHITECTURE", "anchor": "Overview",
                                             "content": "New overview."})
    assert result["data"]["proposal"]["class"] == "intent"


async def test_valid_code_derived_auto_applies_with_notice(agent, repo, human):
    _write_docs(repo)
    await agent.join(repo)
    result = await agent.op("docs_propose", {"doc": "ARCHITECTURE", "anchor": "Technology Stack",
                                             "content": TABLE})
    assert result["data"]["proposal"]["status"] == "applied"
    assert TABLE.splitlines()[2] in (repo / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    recent = await human.op("messages_recent", {})
    assert any("Auto-applied" in m["body"] for m in recent["data"]["messages"])


async def test_misshapen_code_derived_downgrades_to_intent(agent, repo):
    _write_docs(repo)
    await agent.join(repo)
    before = _hash(repo / "docs" / "ARCHITECTURE.md")
    result = await agent.op("docs_propose", {"doc": "ARCHITECTURE", "anchor": "Technology Stack",
                                             "content": "# Oops\n[link](x) and <b>html</b>"})
    assert result["data"]["proposal"]["status"] == "pending"
    assert result["data"]["proposal"]["class"] == "intent"
    assert _hash(repo / "docs" / "ARCHITECTURE.md") == before


async def test_approve_applies_and_reject_leaves_bytes(agent, repo, human):
    _write_docs(repo)
    await agent.join(repo)
    first = await agent.op("docs_propose", {"doc": "RULES", "anchor": "Overview", "content": "Kept."})
    pid = first["data"]["proposal"]["id"]
    before = _hash(repo / "docs" / "RULES.md")
    second = await agent.op("docs_propose", {"doc": "RULES", "anchor": "Overview", "content": "Dropped."})
    assert (await human.op("docs_decide", {"id": second["data"]["proposal"]["id"], "approve": False}))["data"][
        "proposal"]["status"] == "rejected"
    assert _hash(repo / "docs" / "RULES.md") == before
    decided = await human.op("docs_decide", {"id": pid, "approve": True})
    assert decided["data"]["proposal"]["status"] == "applied"
    assert "Kept." in (repo / "docs" / "RULES.md").read_text(encoding="utf-8")


async def test_agent_cannot_decide(agent, repo):
    _write_docs(repo)
    await agent.join(repo)
    pid = (await agent.op("docs_propose", {"doc": "RULES", "anchor": "Overview",
                                           "content": "x"}))["data"]["proposal"]["id"]
    denied = await agent.op("docs_decide", {"id": pid, "approve": True})
    assert denied["error"]["code"] == "human_only"


async def test_approve_after_external_edit_conflicts(agent, repo, human):
    _write_docs(repo)
    await agent.join(repo)
    pid = (await agent.op("docs_propose", {"doc": "RULES", "anchor": "Overview",
                                           "content": "Stale."}))["data"]["proposal"]["id"]
    path = repo / "docs" / "RULES.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nHuman edit.\n", encoding="utf-8")
    decided = await human.op("docs_decide", {"id": pid, "approve": True})
    assert decided["data"]["proposal"]["status"] == "conflicted"
    assert "Stale." not in path.read_text(encoding="utf-8")


async def test_crash_recovery_and_tmp_sweep(agent, repo, state):
    _write_docs(repo)
    await agent.join(repo)
    pid = (await agent.op("docs_propose", {"doc": "RULES", "anchor": "Overview",
                                           "content": "Recovered."}))["data"]["proposal"]["id"]
    db = state.project_db(agent.project_id)
    target = _hash(repo / "docs" / "RULES.md").replace("0", "1", 1)
    (repo / "docs" / ".RULES.md.my-team-tmp").write_bytes(b"leftover")
    db.write_sync(lambda tx: tx.execute("UPDATE doc_proposals SET status = 'applying', target_hash = ? WHERE id = ?",
                                        (target, pid)))
    path = repo / "docs" / "RULES.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nChanged underneath.\n", encoding="utf-8")
    await agent.op("docs_propose", {"doc": "RULES", "anchor": "Goals", "content": "Sweep trigger."})
    listed = await agent.op("docs_proposals", {"status": "conflicted"})
    assert [p["id"] for p in listed["data"]["proposals"]] == [pid]
    assert not (repo / "docs" / ".RULES.md.my-team-tmp").exists()


async def test_recovery_completes_matching_apply(agent, repo, state):
    _write_docs(repo)
    await agent.join(repo)
    pid = (await agent.op("docs_propose", {"doc": "RULES", "anchor": "Overview",
                                           "content": "Recovered."}))["data"]["proposal"]["id"]
    db = state.project_db(agent.project_id)
    current = _hash(repo / "docs" / "RULES.md")
    db.write_sync(lambda tx: tx.execute("UPDATE doc_proposals SET status = 'applying', target_hash = ? WHERE id = ?",
                                        (current, pid)))
    await agent.op("docs_propose", {"doc": "RULES", "anchor": "Goals", "content": "Sweep trigger."})
    listed = await agent.op("docs_proposals", {"status": "applied"})
    assert pid in [p["id"] for p in listed["data"]["proposals"]]


async def test_listing_filters_status(agent, repo):
    _write_docs(repo)
    await agent.join(repo)
    await agent.op("docs_propose", {"doc": "RULES", "anchor": "Overview", "content": "One."})
    await agent.op("docs_propose", {"doc": "PRD", "anchor": "Goals", "content": "Two."})
    assert len((await agent.op("docs_proposals", {}))["data"]["proposals"]) == 2
    assert len((await agent.op("docs_proposals", {"status": "pending"}))["data"]["proposals"]) == 2

import sqlite3

import pytest

from my_team.db import backup

pytestmark = pytest.mark.anyio

_H = {"X-My-Team": "1"}


async def _write(agent, title="Secret plan", body="Launch at dawn"):
    return (await agent.op("memory_write", {"kind": "fact", "title": title, "body": body}))["data"]["memory"]


async def _purge(http, project_id, memory_id):
    return await http.post(f"/api/v1/projects/{project_id}/memory_purge", json={"id": memory_id}, headers=_H)


async def test_purge_overwrites_and_clears_search(agent, repo, state, http, human):
    await agent.join(repo)
    memory = await _write(agent)
    assert (await agent.op("memory_search", {"q": "dawn"}))["data"]["memories"]
    backup.backup_all(force=True)
    purged = (await _purge(http, agent.project_id, memory["id"])).json()["data"]
    assert purged["memory"]["title"] == "[purged]" and purged["memory"]["body"] == "[purged]"
    assert (await agent.op("memory_search", {"q": "dawn"}))["data"]["memories"] == []
    assert purged["affected_backups"] and all(n.startswith(agent.project_id) for n in purged["affected_backups"])
    flags = state.project_db(agent.project_id).read_sync(
        lambda tx: tx.all("SELECT backup_name FROM purge_flags WHERE memory_id = ?", (memory["id"],)))
    assert sorted(r[0] for r in flags) == sorted(purged["affected_backups"])


async def test_purge_vacuums_freelist(agent, repo, state, http, human):
    await agent.join(repo)
    big = [(await _write(agent, title=f"Big{i}", body="x" * 7000))["id"] for i in range(5)]
    keeper = await _write(agent, title="Keeper", body="y" * 7000)
    db = state.project_db(agent.project_id)
    for memory_id in big:
        db.write_sync(lambda tx, mid=memory_id: tx.execute("DELETE FROM memories WHERE id = ?", (mid,)))
    assert db.read_sync(lambda tx: tx.scalar("PRAGMA freelist_count")) > 0
    await _purge(http, agent.project_id, keeper["id"])
    assert db.read_sync(lambda tx: tx.scalar("PRAGMA freelist_count")) == 0


async def test_purge_needs_human(agent, repo, http):
    await agent.join(repo)
    memory = await _write(agent)
    denied = await agent.raw(f"/api/v1/projects/{agent.project_id}/memory_purge", {"id": memory["id"]})
    assert denied.json()["error"]["code"] == "human_only"


async def test_restore_roundtrip(agent, repo, state, http, human):
    await agent.join(repo)
    await _write(agent, title="Keepme", body="aaa")
    backup.backup_all(force=True)
    backups = (await http.post(f"/api/v1/projects/{agent.project_id}/db_backups", json={},
                               headers=_H)).json()["data"]["backups"]
    assert len(backups) == 1
    await _write(agent, title="Loseme", body="bbb")
    before = state.project_db(agent.project_id).read_sync(
        lambda tx: tx.scalar("SELECT value FROM meta WHERE key = 'db_epoch'"))
    restored = (await http.post(f"/api/v1/projects/{agent.project_id}/db_restore",
                                 json={"backup": backups[0]["name"]}, headers=_H)).json()["data"]
    assert restored["db_epoch"] != before
    keep = [m["title"] for m in (await agent.op("memory_search", {"q": "Keepme"}))["data"]["memories"]]
    gone = (await agent.op("memory_search", {"q": "Loseme"}))["data"]["memories"]
    assert "Keepme" in keep and gone == []


async def test_restore_rejects_unreadable_corrupt_and_new_schema(agent, repo, state, http, human):
    await agent.join(repo)
    await _write(agent, title="Keepme")
    folder = backup.private_data_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{agent.project_id}-2099-01-01.db").write_bytes(b"not a database" * 100)
    garbage = await http.post(f"/api/v1/projects/{agent.project_id}/db_restore",
                              json={"backup": f"{agent.project_id}-2099-01-01.db"}, headers=_H)
    assert garbage.json()["error"]["code"] == "unreadable_backup"
    damaged = folder / f"{agent.project_id}-2099-01-02.db"
    live = backup.projects_dir() / f"{agent.project_id}.db"
    with sqlite3.connect(live) as conn:
        conn.execute("VACUUM INTO ?", (str(damaged),))
    with sqlite3.connect(damaged) as conn:
        conn.execute("DELETE FROM memories_fts_data")
        conn.commit()
    corrupt = await http.post(f"/api/v1/projects/{agent.project_id}/db_restore",
                              json={"backup": damaged.name}, headers=_H)
    assert corrupt.json()["error"]["code"] == "corrupt_backup"
    newer = folder / f"{agent.project_id}-2099-01-03.db"
    with sqlite3.connect(live) as conn:
        conn.execute("VACUUM INTO ?", (str(newer),))
    with sqlite3.connect(newer) as conn:
        conn.execute("PRAGMA user_version = 9999")
    mismatch = await http.post(f"/api/v1/projects/{agent.project_id}/db_restore",
                               json={"backup": newer.name}, headers=_H)
    assert mismatch.json()["error"]["code"] == "schema_mismatch"
    keep = [m["title"] for m in (await agent.op("memory_search", {"q": "Keepme"}))["data"]["memories"]]
    assert "Keepme" in keep


async def test_restore_rejects_traversal_and_unknown_project(agent, repo, http, human):
    await agent.join(repo)
    denied = await http.post(f"/api/v1/projects/{agent.project_id}/db_restore", json={"backup": "../x.db"},
                             headers=_H)
    assert denied.json()["error"]["code"] == "bad_backup"
    missing = await http.post("/api/v1/projects/does-not-exist/db_backups", json={}, headers=_H)
    assert missing.json()["error"]["code"] == "unknown_project"

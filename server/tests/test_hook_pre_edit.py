import io
import json
import sys

import anyio
import pytest

from my_team import activation, auth, hook
from my_team.clock import now_ms
from tests.conftest import Agent

pytestmark = pytest.mark.anyio


class _SyncClient:
    def __init__(self, app, state):
        self.app, self.state = app, state

    def _call(self, path, payload, session_id=None):
        from starlette.testclient import TestClient
        body = json.dumps(payload).encode()
        headers = auth.signed_headers(self.state.secret, "POST", path, body, now_ms(), session_id)
        headers["Content-Type"] = "application/json"
        headers["Host"] = f"127.0.0.1:{self.state.port}"
        with TestClient(self.app) as client:
            envelope = client.post(path, content=body, headers=headers).json()
        assert envelope.get("success"), envelope
        return envelope.get("data") or {}

    def registry(self, name, payload):
        return self._call(f"/api/v1/registry/{name}", payload)

    def project(self, project_id, name, payload, session_id=None):
        return self._call(f"/api/v1/projects/{project_id}/{name}", payload, session_id)


@pytest.fixture
def hooked(state, monkeypatch):
    from my_team.daemon.app import create_app
    monkeypatch.setattr(hook, "connect", lambda: _SyncClient(create_app(state), state))


async def _opencode(http, state, repo, native_id):
    return await Agent(http, state, agent_type="opencode", native_id=native_id).join(repo)


async def _claimed(human, agent, title, paths):
    ticket = (await human.op("ticket_create", {"title": title, "status": "ready",
                                               "acceptance_criteria": ["done"]}))["data"]["ticket"]
    result = await agent.op("ticket_claim", {"key": ticket["key"], "paths": paths})
    assert result["success"], result
    return ticket


def _payload(repo, native_id, rel):
    return {"session_id": native_id, "cwd": str(repo), "file_path": str(repo / rel)}


async def _check(native_id, repo, rel):
    return await anyio.to_thread.run_sync(hook.pre_edit_text, "opencode", _payload(repo, native_id, rel))


async def test_ask_with_no_claimed_ticket(hooked, http, state, repo):
    activation.set_scope("directory", True, cwd=str(repo))
    await _opencode(http, state, repo, "op-1")
    reason = await _check("op-1", repo, "src/app.py")
    assert reason and "no claimed ticket" in reason


async def test_allow_after_claim(hooked, http, state, repo, human):
    activation.set_scope("directory", True, cwd=str(repo))
    agent = await _opencode(http, state, repo, "op-1")
    await _claimed(human, agent, "Work", ["src/"])
    assert await _check("op-1", repo, "src/app.py") is None


async def test_ask_on_another_sessions_claimed_paths(hooked, http, state, repo, human):
    activation.set_scope("directory", True, cwd=str(repo))
    me = await _opencode(http, state, repo, "op-1")
    other = await _opencode(http, state, repo, "op-2")
    await _claimed(human, other, "API work", ["src/api"])
    await _claimed(human, me, "UI work", ["ui/"])
    assert await _check("op-1", repo, "ui/Board.tsx") is None
    theirs = await _check("op-1", repo, "src/api/users.py")
    assert theirs and "opencode-2" in theirs


async def test_allow_under_docs(hooked, http, state, repo):
    activation.set_scope("directory", True, cwd=str(repo))
    await _opencode(http, state, repo, "op-1")
    assert await _check("op-1", repo, "docs/PRD.md") is None


async def test_silent_when_activation_off(http, state, repo, tmp_path):
    await _opencode(http, state, repo, "op-1")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    payload = {"session_id": "op-1", "cwd": str(elsewhere), "file_path": str(elsewhere / "notes.py")}
    assert await anyio.to_thread.run_sync(hook.pre_edit_text, "opencode", payload) is None


async def test_silent_when_daemon_unreachable(http, state, repo):
    activation.set_scope("directory", True, cwd=str(repo))
    await _opencode(http, state, repo, "op-1")
    assert await _check("op-1", repo, "src/app.py") is None


async def test_run_prints_reason_only_on_ask(hooked, http, state, repo, human, monkeypatch, capsys):
    activation.set_scope("directory", True, cwd=str(repo))
    agent = await _opencode(http, state, repo, "op-1")
    payload = _payload(repo, "op-1", "src/app.py")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert await anyio.to_thread.run_sync(hook.run, "pre-edit", "opencode") == 0
    assert "no claimed ticket" in capsys.readouterr().out
    await _claimed(human, agent, "Work", ["src/"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert await anyio.to_thread.run_sync(hook.run, "pre-edit", "opencode") == 0
    assert capsys.readouterr().out == ""

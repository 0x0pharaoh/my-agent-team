import json
import os
import subprocess
import sys
import time
import tomllib
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from my_team.client import connect
from tests.conftest import PASSPHRASE

pytestmark = pytest.mark.anyio
LIVE_PORT = 47388


@pytest.fixture
def live(home, repo):
    env = os.environ | {"MY_TEAM_HOME": str(home), "MY_TEAM_PORT": str(LIVE_PORT)}
    daemon = subprocess.Popen([sys.executable, "-m", "my_team", "serve"], env=env)
    deadline = time.monotonic() + 20
    while True:
        try:
            client = connect(autostart=False)
            break
        except Exception:
            if time.monotonic() > deadline or daemon.poll() is not None:
                daemon.kill()
                raise
            time.sleep(0.2)
    client.registry("auth_setup", {"passphrase": PASSPHRASE})
    yield env
    daemon.terminate()
    daemon.wait(timeout=20)


def shim(env: dict, repo, native_env: dict | None = None) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "my_team", "mcp"], cwd=str(repo),
                                 env=env | (native_env or {}))


def text(result) -> str:
    return result.content[0].text


@asynccontextmanager
async def human_client():
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{LIVE_PORT}", headers={"X-My-Team": "1"}) as client:
        login = await client.post("/auth/login", json={"passphrase": PASSPHRASE})
        assert login.status_code == 200, login.text
        yield client


async def test_claude_code_session_end_to_end(live, repo):
    claude = shim(live, repo, {"CLAUDE_CODE_SESSION_ID": "cc-session-1"})
    async with stdio_client(claude) as (read, write):
        async with ClientSession(read, write, client_info=types.Implementation(name="claude-code", version="t")) as mcp:
            await mcp.initialize()
            names = {tool.name for tool in (await mcp.list_tools()).tools}
            assert {"team_context", "ticket_claim", "hook_prompt", "team_init"} <= names

            inactive = await mcp.call_tool("hook_prompt", {"session_id": "cc-session-1", "cwd": str(repo)})
            assert text(inactive) == "{}"

            init = await mcp.call_tool("team_init", {"key": "MT"})
            assert "created" in text(init), text(init)
            project_id = tomllib.loads((repo / ".my-team" / "project.toml").read_text())["id"]

            first = json.loads(text(await mcp.call_tool("hook_prompt", {"session_id": "cc-session-1",
                                                                         "cwd": str(repo)})))
            context = first["hookSpecificOutput"]["additionalContext"]
            assert "[my-team] active (directory)" in context and "Protocol:" in context

            async with human_client() as human:
                board = (await human.post(f"/api/v1/projects/{project_id}/board", json={})).json()["data"]
                seat = next(a for a in board["agents"] if a["agent_type"] == "claude-code")
                assert seat["sessions"][0]["status"] == "active"
                created = (await human.post(f"/api/v1/projects/{project_id}/ticket_create", json={
                    "title": "Add login", "status": "ready", "acceptance_criteria": ["form posts"]})).json()
                key = created["data"]["ticket"]["key"]
                await human.post(f"/api/v1/projects/{project_id}/ticket_assign", json={"key": key,
                                                                                    "agent_id": seat["id"]})

                delta = json.loads(text(await mcp.call_tool("hook_prompt", {"session_id": "cc-session-1",
                                                                             "cwd": str(repo)})))
                assert f"Assigned to you, not started: {key}" in delta["hookSpecificOutput"]["additionalContext"]

                claimed = json.loads(text(await mcp.call_tool("ticket_claim", {"key": key})))
                assert claimed["ticket"]["status"] == "in_progress"

                cleared = json.loads(text(await mcp.call_tool("hook_prompt", {"session_id": "cc-session-2",
                                                                               "cwd": str(repo)})))
                assert f"Active ticket: {key}" in cleared["hookSpecificOutput"]["additionalContext"]

                await human.post(f"/api/v1/projects/{project_id}/ticket_transition", json={"key": key,
                                                                                        "action": "pause"})
                stopped = json.loads(text(await mcp.call_tool("hook_prompt", {"session_id": "cc-session-2",
                                                                               "cwd": str(repo)})))
                assert f"STOP: {key}" in stopped["hookSpecificOutput"]["additionalContext"]


async def test_codex_sessions_are_keyed_by_thread(live, repo):
    async with stdio_client(shim(live, repo)) as (read, write):
        async with ClientSession(read, write, client_info=types.Implementation(name="codex-mcp-client",
                                                                               version="t")) as mcp:
            await mcp.initialize()
            names = {tool.name for tool in (await mcp.list_tools()).tools}
            assert "hook_prompt" in names
            await mcp.call_tool("team_init", {"key": "CX"}, meta={"threadId": "thread-a"})
            a = json.loads(text(await mcp.call_tool("team_context", {}, meta={"threadId": "thread-a"})))
            b = json.loads(text(await mcp.call_tool("team_context", {}, meta={"threadId": "thread-b"})))
            assert a["you"]["session_id"] != b["you"]["session_id"]
            assert a["you"]["agent"] == "codex" and b["you"]["agent"] == "codex-2"


async def test_dashboard_stream_replays_and_follows(live, repo):
    async with stdio_client(shim(live, repo, {"CLAUDE_CODE_SESSION_ID": "cc-s"})) as (read, write):
        async with ClientSession(read, write, client_info=types.Implementation(name="claude-code", version="t")) as mcp:
            await mcp.initialize()
            await mcp.call_tool("team_init", {"key": "ST"})
            await mcp.call_tool("team_context", {})
            async with human_client() as human:
                projects = (await human.post("/api/v1/registry/projects_list", json={})).json()["data"]["projects"]
                project_id = projects[0]["id"]
                created = (await human.post(f"/api/v1/projects/{project_id}/ticket_create", json={"title": "One"}))
                cursor = created.json()["meta"]["cursor"]
                epoch = cursor.split(".")[0]
                seen = []
                async with human.stream("GET", "/api/v1/events/stream",
                                        params={"cursors": f"{project_id}:{epoch}.0"}) as stream:
                    async for line in stream.aiter_lines():
                        if line.startswith("data:") and '"type"' in line:
                            seen.append(json.loads(line[5:])["type"])
                            if seen.count("ticket.created") == 1 and seen[-1] == "ticket.created":
                                await human.post(f"/api/v1/projects/{project_id}/ticket_create", json={"title": "Two"})
                            if seen.count("ticket.created") == 2:
                                break
                assert "session.started" in seen and seen.count("ticket.created") == 2

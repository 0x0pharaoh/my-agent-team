import os
import sys

import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.anyio


def shim(repo) -> StdioServerParameters:
    env = os.environ | {"MY_TEAM_HOME": os.environ["MY_TEAM_HOME"]}
    return StdioServerParameters(command=sys.executable, args=["-m", "my_team", "mcp"], cwd=str(repo), env=env)


def prompt_text(result) -> str:
    assert len(result.messages) == 1
    assert result.messages[0].role == "user"
    return result.messages[0].content.text


async def test_opencode_sees_three_prompts(repo):
    async with stdio_client(shim(repo)) as (read, write):
        async with ClientSession(read, write,
                                 client_info=types.Implementation(name="opencode", version="t")) as mcp:
            await mcp.initialize()
            assert {p.name for p in (await mcp.list_prompts()).prompts} == {"init", "active", "audit"}
            assert "team_init" in prompt_text(await mcp.get_prompt("init"))
            assert "team_active" in prompt_text(await mcp.get_prompt("active", {"args": "session"}))
            assert "session" in prompt_text(await mcp.get_prompt("active", {"args": "session"}))
            assert "S4" in prompt_text(await mcp.get_prompt("audit"))


async def test_claude_code_sees_no_prompts(repo):
    async with stdio_client(shim(repo)) as (read, write):
        async with ClientSession(read, write,
                                 client_info=types.Implementation(name="claude-code", version="t")) as mcp:
            await mcp.initialize()
            assert (await mcp.list_prompts()).prompts == []
            with pytest.raises(Exception):
                await mcp.get_prompt("init")

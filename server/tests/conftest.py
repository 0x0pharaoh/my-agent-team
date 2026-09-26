import json
import subprocess

import httpx
import pytest

from my_team import auth
from my_team.clock import now_ms

PORT = 47399
BASE = f"http://127.0.0.1:{PORT}"
PASSPHRASE = "correct horse battery"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TEAM_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


@pytest.fixture
def state():
    from my_team.daemon.state import DaemonState
    daemon = DaemonState(port=PORT)
    yield daemon
    daemon.close()


@pytest.fixture
async def http(state):
    from my_team.daemon.app import create_app
    transport = httpx.ASGITransport(app=create_app(state))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        yield client


class Agent:
    def __init__(self, http, state, agent_type="claude-code", native_id="native-1"):
        self.http, self.state = http, state
        self.agent_type, self.native_id = agent_type, native_id
        self.project_id = None
        self.session_id = None

    async def raw(self, path: str, payload: dict, session: str | None = None) -> httpx.Response:
        body = json.dumps(payload).encode()
        headers = auth.signed_headers(self.state.secret, "POST", path, body, now_ms(), session)
        headers["Content-Type"] = "application/json"
        return await self.http.post(path, content=body, headers=headers)

    async def registry(self, name: str, payload: dict) -> dict:
        response = await self.raw(f"/api/v1/registry/{name}", payload)
        return response.json()

    async def op(self, name: str, payload: dict | None = None) -> dict:
        response = await self.raw(f"/api/v1/projects/{self.project_id}/{name}", payload or {}, self.session_id)
        return response.json()

    async def join(self, repo) -> "Agent":
        init = await self.registry("project_init", {"cwd": str(repo)})
        assert init["success"], init
        self.project_id = init["data"]["project"]["id"]
        registered = await self.op("session_register", {"agent_type": self.agent_type, "native_id": self.native_id,
                                                         "root_path": str(repo)})
        assert registered["success"], registered
        self.session_id = registered["data"]["session_id"]
        return self


class Human:
    def __init__(self, http, project_id: str | None = None):
        self.http = http
        self.project_id = project_id

    async def op(self, name: str, payload: dict | None = None) -> dict:
        response = await self.http.post(f"/api/v1/projects/{self.project_id}/{name}", json=payload or {},
                                        headers={"X-My-Team": "1"})
        return response.json()


@pytest.fixture
async def agent(http, state, repo):
    return await Agent(http, state).join(repo)


@pytest.fixture
async def human(http, state, agent):
    setup = await agent.registry("auth_setup", {"passphrase": PASSPHRASE})
    assert setup["success"], setup
    login = await http.post("/auth/login", json={"passphrase": PASSPHRASE}, headers={"X-My-Team": "1"})
    assert login.status_code == 200, login.text
    return Human(http, agent.project_id)

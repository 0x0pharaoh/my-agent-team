import json

import pytest

from my_team import auth
from my_team.clock import now_ms
from tests.conftest import BASE, PORT

pytestmark = pytest.mark.anyio


async def test_security_headers_on_every_response(http):
    response = await http.get("/health")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_rebound_host_is_rejected(http):
    response = await http.get("/health", headers={"Host": f"evil.example:{PORT}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_host"


async def test_api_requires_custom_header(http, agent):
    response = await http.post(f"/api/v1/projects/{agent.project_id}/board", json={})
    assert response.json()["error"]["code"] == "missing_header"


async def test_cross_origin_is_rejected(http, agent):
    response = await http.post(f"/api/v1/projects/{agent.project_id}/board", json={},
                               headers={"X-My-Team": "1", "Origin": "http://127.0.0.1:3000"})
    assert response.json()["error"]["code"] == "bad_origin"


async def test_same_origin_is_accepted_for_the_dashboard(http, human):
    response = await http.post(f"/api/v1/projects/{human.project_id}/board", json={},
                               headers={"X-My-Team": "1", "Origin": BASE})
    assert response.json()["success"]


async def test_unsigned_unauthenticated_request_needs_login(http, agent):
    http.cookies.clear()
    response = await http.post(f"/api/v1/projects/{agent.project_id}/board", json={}, headers={"X-My-Team": "1"})
    assert response.status_code == 401


async def test_tampered_signature_is_rejected(agent):
    response = await agent.raw(f"/api/v1/projects/{agent.project_id}/team_context", {}, agent.session_id)
    assert response.json()["success"]
    path = f"/api/v1/projects/{agent.project_id}/team_context"
    headers = auth.signed_headers(agent.state.secret, "POST", path, b"{}", now_ms(), agent.session_id)
    headers["Content-Type"] = "application/json"
    forged = await agent.http.post(path, content=json.dumps({"delta": True}).encode(), headers=headers)
    assert forged.json()["error"]["code"] == "bad_signature"


async def test_replayed_signature_is_rejected(agent):
    path = f"/api/v1/projects/{agent.project_id}/team_context"
    body = json.dumps({}).encode()
    headers = auth.signed_headers(agent.state.secret, "POST", path, body, now_ms(), agent.session_id)
    headers["Content-Type"] = "application/json"
    assert (await agent.http.post(path, content=body, headers=headers)).json()["success"]
    replay = await agent.http.post(path, content=body, headers=headers)
    assert replay.json()["error"]["code"] == "bad_signature"


@pytest.mark.parametrize("op", ["ticket_transition", "ticket_assign", "ticket_edit", "board", "agent_update"])
async def test_agents_cannot_run_human_only_operations(agent, op):
    response = await agent.op(op, {})
    assert response["error"]["code"] == "human_only"


async def test_humans_cannot_claim(human):
    response = await human.op("ticket_claim", {"key": "MT-1"})
    assert response["error"]["code"] == "agent_only"


async def test_passphrase_cannot_be_overwritten_by_an_agent(agent, human):
    again = await agent.registry("auth_setup", {"passphrase": "attacker passphrase"})
    assert again["error"]["code"] == "passphrase_exists"


async def test_wrong_passphrase_is_refused(http, human):
    http.cookies.clear()
    response = await http.post("/auth/login", json={"passphrase": "wrong wrong wrong"}, headers={"X-My-Team": "1"})
    assert response.status_code == 401


async def test_health_proves_knowledge_of_the_secret(http, state):
    body = (await http.get("/health", params={"nonce": "abcdef0123456789"})).json()
    assert body["proof"] == auth.health_proof(state.secret, "abcdef0123456789", PORT)

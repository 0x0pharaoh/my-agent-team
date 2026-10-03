import hashlib
import json
import secrets

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from my_team.passkeys import b64u_decode, b64u_encode
from tests.conftest import BASE, PASSPHRASE

pytestmark = pytest.mark.anyio

RP_ID = "localhost"
ORIGIN = "http://127.0.0.1:47399"
_H = {"X-My-Team": "1"}


def _key():
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
    return private, cose


def _auth_data(cose=None, sign_count=0, credential_id=b""):
    flags = 0x45 if cose is not None else 0x01
    data = hashlib.sha256(RP_ID.encode()).digest() + bytes([flags]) + sign_count.to_bytes(4, "big")
    if cose is not None:
        data += b"\x00" * 16 + len(credential_id).to_bytes(2, "big") + credential_id + cose
    return data


def _client_data(kind, challenge, origin=ORIGIN):
    return json.dumps({"type": kind, "challenge": challenge, "origin": origin}).encode()


def _registration_credential(challenge, origin=ORIGIN):
    private, cose = _key()
    credential_id = secrets.token_bytes(32)
    auth_data = _auth_data(cose, 0, credential_id)
    client_data = _client_data("webauthn.create", challenge, origin)
    attestation = cbor2.dumps({"fmt": "none", "authData": auth_data, "attStmt": {}})
    return private, {"id": b64u_encode(credential_id), "rawId": b64u_encode(credential_id), "type": "public-key",
                     "response": {"clientDataJSON": b64u_encode(client_data),
                                  "attestationObject": b64u_encode(attestation)}}


def _assertion_credential(private, credential_id, challenge, sign_count=1, origin=ORIGIN):
    auth_data = _auth_data(sign_count=sign_count)
    client_data = _client_data("webauthn.get", challenge, origin)
    signature = private.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
    return {"id": b64u_encode(credential_id), "rawId": b64u_encode(credential_id), "type": "public-key",
            "response": {"clientDataJSON": b64u_encode(client_data),
                         "authenticatorData": b64u_encode(auth_data), "signature": b64u_encode(signature)}}


def _challenge_of(options):
    return options["challenge"]


async def _register(http, name="Desk key"):
    options = (await http.post("/auth/passkey/register/options", json={}, headers=_H)).json()["data"]["options"]
    assert options["rp"]["id"] == RP_ID
    private, credential = _registration_credential(_challenge_of(options))
    finish = await http.post("/auth/passkey/register/finish", json={"credential": credential, "name": name}, headers=_H)
    assert finish.json()["success"], finish.json()
    return private, b64u_decode(credential["id"])


async def test_full_flow_register_login_remove(http, human):
    private, credential_id = await _register(http)
    listed = (await http.get("/auth/passkey/list", headers=_H)).json()["data"]["passkeys"]
    assert len(listed) == 1 and listed[0]["name"] == "Desk key"
    options = (await http.post("/auth/passkey/login/options", json={}, headers=_H)).json()["data"]["options"]
    assertion = _assertion_credential(private, credential_id, _challenge_of(options))
    finish = await http.post("/auth/passkey/login/finish", json={"credential": assertion}, headers=_H)
    assert finish.json()["success"], finish.json()
    assert "mt_session" in finish.cookies
    me = await http.get("/auth/me", headers=_H)
    assert me.json()["data"]["authenticated"] is True
    removed = await http.post("/auth/passkey/remove", json={"id": listed[0]["id"]}, headers=_H)
    assert removed.json()["success"]
    assert (await http.get("/auth/passkey/list", headers=_H)).json()["data"]["passkeys"] == []
    retry = await http.post("/auth/passkey/login/finish", json={"credential": assertion}, headers=_H)
    assert retry.json()["error"]["code"] in ("stale_challenge", "unknown_credential")


async def test_passphrase_still_works(http, human):
    login = await http.post("/auth/login", json={"passphrase": PASSPHRASE}, headers={"X-My-Team": "1"})
    assert login.status_code == 200


async def test_wrong_origin_fails(http, human):
    private, credential_id = await _register(http)
    options = (await http.post("/auth/passkey/login/options", json={}, headers=_H)).json()["data"]["options"]
    evil = _assertion_credential(private, credential_id, _challenge_of(options), origin="https://evil.test")
    bad = await http.post("/auth/passkey/login/finish", json={"credential": evil}, headers=_H)
    assert bad.json()["success"] is False


async def test_sign_count_regression_rejected(http, human):
    private, credential_id = await _register(http)
    options = (await http.post("/auth/passkey/login/options", json={}, headers=_H)).json()["data"]["options"]
    first = _assertion_credential(private, credential_id, _challenge_of(options), sign_count=5)
    assert (await http.post("/auth/passkey/login/finish", json={"credential": first}, headers=_H)).json()["success"]
    options = (await http.post("/auth/passkey/login/options", json={}, headers=_H)).json()["data"]["options"]
    cloned = _assertion_credential(private, credential_id, _challenge_of(options), sign_count=0)
    failed = await http.post("/auth/passkey/login/finish", json={"credential": cloned}, headers=_H)
    assert failed.json()["error"]["code"] == "bad_passkey"


async def test_protected_routes_need_login(state):
    import httpx
    from my_team.daemon.app import create_app
    transport = httpx.ASGITransport(app=create_app(state))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as bare:
        for method, path, body in (("POST", "/auth/passkey/register/options", {}),
                                   ("POST", "/auth/passkey/register/finish", {"credential": {}}),
                                   ("GET", "/auth/passkey/list", None),
                                   ("POST", "/auth/passkey/remove", {"id": "x"})):
            if body is not None:
                response = await bare.request(method, path, json=body, headers=_H)
            else:
                response = await bare.get(path, headers=_H)
            assert response.json()["error"]["code"] == "login_required"


async def test_signed_agent_calls_get_human_only_not_bad_signature(agent, repo):
    await agent.join(repo)
    denied = await agent.raw(f"/api/v1/projects/{agent.project_id}/memory_purge", {"id": "x"})
    assert denied.json()["error"]["code"] == "human_only"


async def test_exclude_lists_registered_credential(http, human):
    await _register(http)
    options = (await http.post("/auth/passkey/register/options", json={}, headers=_H)).json()["data"]["options"]
    assert len(options["excludeCredentials"]) == 1

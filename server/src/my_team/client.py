import http.client
import json
import os
import secrets
import subprocess
import sys
import time

from my_team import auth, config
from my_team.clock import now_ms
from my_team.paths import private_data_dir

API_VERSION = 1
_DETACHED, _NEW_GROUP, _NO_WINDOW, _BREAKAWAY = 0x8, 0x200, 0x08000000, 0x01000000


class DaemonUnavailable(Exception):
    pass


class DaemonError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


def _server_info() -> dict | None:
    try:
        return json.loads((private_data_dir() / "server.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _verified(info: dict, secret: bytes) -> bool:
    nonce = secrets.token_hex(16)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=2)
        conn.request("GET", f"/health?nonce={nonce}", headers={"Host": f"127.0.0.1:{info['port']}"})
        body = json.loads(conn.getresponse().read())
    except (OSError, ValueError, KeyError):
        return False
    return (body.get("app") == "my-team" and body.get("instance") == info.get("instance")
            and body.get("proof") == auth.health_proof(secret, nonce, info["port"]))


def spawn_daemon() -> None:
    command = [sys.executable, "-m", "my_team", "serve"]
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        flags = _DETACHED | _NEW_GROUP | _NO_WINDOW
        try:
            subprocess.Popen(command, creationflags=flags | _BREAKAWAY, **options)
        except OSError:
            subprocess.Popen(command, creationflags=flags, **options)
    else:
        subprocess.Popen(command, start_new_session=True, **options)


def connect(autostart: bool | None = None, wait_s: float = 15) -> "Client":
    secret = auth.load_secret()
    info = _server_info()
    if info and _verified(info, secret):
        return Client(info["port"], secret)
    if autostart is None:
        autostart = config.load()["autostart"]
    if not autostart:
        raise DaemonUnavailable("my-team server is not running: run `my-team serve`, or enable autostart with "
                                "`my-team install <agent>`.")
    spawn_daemon()
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        time.sleep(0.2)
        info = _server_info()
        if info and _verified(info, secret):
            return Client(info["port"], secret)
    raise DaemonUnavailable("my-team server did not start; run `my-team serve` to see why.")


class Client:
    def __init__(self, port: int, secret: bytes):
        self.port = port
        self.secret = secret

    def call(self, path: str, payload: dict, session_id: str | None = None) -> tuple[dict, dict]:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = auth.signed_headers(self.secret, "POST", path, body, now_ms(), session_id)
        headers.update({"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"})
        try:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
            conn.request("POST", path, body=body, headers=headers)
            response = conn.getresponse()
            envelope = json.loads(response.read())
        except (OSError, ValueError) as exc:
            raise DaemonUnavailable(f"my-team server did not answer: {exc}") from exc
        if not envelope.get("success"):
            error = envelope.get("error") or {}
            raise DaemonError(response.status, error.get("code", "error"), error.get("message", "request failed"),
                              error.get("details"))
        return envelope.get("data") or {}, envelope.get("meta") or {}

    def registry(self, name: str, payload: dict) -> dict:
        return self.call(f"/api/v1/registry/{name}", payload)[0]

    def project(self, project_id: str, name: str, payload: dict, session_id: str | None = None) -> dict:
        return self.project_with_meta(project_id, name, payload, session_id)[0]

    def project_with_meta(self, project_id: str, name: str, payload: dict,
                          session_id: str | None = None) -> tuple[dict, dict]:
        return self.call(f"/api/v1/projects/{project_id}/{name}", payload, session_id)

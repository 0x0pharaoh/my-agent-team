import asyncio
import json
import logging
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from my_team import __version__, auth
from my_team.actor import HUMAN, Actor, agent_actor
from my_team.clock import now_ms
from my_team.daemon.state import DaemonState, db_epoch
from my_team.db.engine import SchemaTooNew
from my_team.domain import events, notices, projects, sessions
from my_team.errors import DomainError, Forbidden, NotFound, Unauthorized, Unavailable
from my_team.ops import AGENT_INFRA, NO_NOTICES, NOT_ACTIVITY, OPS, REGISTRY_OPS, Ctx, RegistryCtx

log = logging.getLogger("my_team.daemon")
API_VERSION = 1
SESSION_COOKIE = "mt_session"
SESSION_TTL_MS = 12 * 3600_000
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SECURITY_HEADERS = [(k.encode(), v.encode()) for k, v in (
    ("content-security-policy", CSP), ("x-frame-options", "DENY"), ("referrer-policy", "no-referrer"),
    ("x-content-type-options", "nosniff"), ("cross-origin-opener-policy", "same-origin"),
    ("cross-origin-resource-policy", "same-origin"))]
PROTECTED_PREFIXES = ("/api/", "/auth/")


def envelope(data=None, error: dict | None = None, meta: dict | None = None, status: int = 200) -> JSONResponse:
    body = {"success": error is None, "data": data, "error": error, "meta": meta or {}}
    return JSONResponse(body, status_code=status, headers={"cache-control": "no-store"})


def fail(exc: DomainError) -> JSONResponse:
    return envelope(error=exc.to_dict(), status=exc.status)


class Guard:
    """Host allowlist, CSRF header, origin check, and security headers on every response."""

    def __init__(self, app, state: DaemonState):
        self.app = app
        self.state = state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        problem = self._check(headers, scope["path"])
        if problem:
            return await fail(problem)(scope, receive, send)

        async def send_secured(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *SECURITY_HEADERS]
            await send(message)

        await self.app(scope, receive, send_secured)

    def _check(self, headers: dict, path: str) -> DomainError | None:
        if headers.get("host") not in self.state.allowed_hosts:
            return Forbidden("bad_host", "Requests must address the daemon by its loopback name.")
        origin = headers.get("origin")
        if origin and origin not in self.state.allowed_origins:
            return Forbidden("bad_origin", "Cross-origin requests are not accepted.")
        if path.startswith(PROTECTED_PREFIXES) and headers.get("x-my-team") != "1":
            return Forbidden("missing_header", "API requests must send X-My-Team: 1.")
        return None


def create_app(state: DaemonState, static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="my-team", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    failures: dict[str, float] = {"count": 0, "until": 0.0}

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc: DomainError):
        return fail(exc)

    async def human_valid(token: str | None) -> bool:
        if not token:
            return False
        digest, now = auth.token_hash(token), now_ms()
        return bool(await state.registry.read(lambda tx: tx.scalar(
            "SELECT 1 FROM human_sessions WHERE token_hash = ? AND revoked_ms IS NULL AND expires_ms > ?",
            (digest, now))))

    async def authenticate(request: Request, body: bytes) -> Actor:
        if request.headers.get(auth.HEADER_SIG):
            now = now_ms()
            if not auth.verify_signature(state.secret, request.method, request.url.path, request.headers, body, now,
                                         state.nonces):
                raise Unauthorized("bad_signature", "Request signature is invalid or expired.")
            state.last_agent_ms = now
            return Actor("agent", "unresolved", request.headers.get(auth.HEADER_SESSION))
        if await human_valid(request.cookies.get(SESSION_COOKIE)):
            return HUMAN
        raise Unauthorized("login_required", "Log in to the my-team dashboard.")

    def parse(model, body: bytes):
        try:
            return model.model_validate(json.loads(body or b"{}"))
        except (ValueError, ValidationError) as exc:
            errors = exc.errors(include_url=False, include_input=False) if isinstance(exc, ValidationError) else []
            raise DomainError("invalid_input", "Request body does not match the operation's schema.",
                              errors=errors) from None

    @app.get("/health")
    async def health(nonce: str = ""):
        proof = auth.health_proof(state.secret, nonce, state.port) if 8 <= len(nonce) <= 128 else None
        return {"app": "my-team", "version": __version__, "api_version": API_VERSION, "instance": state.instance,
                "proof": proof}

    @app.get("/auth/me")
    async def me(request: Request):
        has_passphrase = await state.registry.read(lambda tx: tx.scalar("SELECT 1 FROM human_auth WHERE id = 1"))
        return envelope({"authenticated": await human_valid(request.cookies.get(SESSION_COOKIE)),
                         "setup_required": not has_passphrase})

    @app.post("/auth/login")
    async def login(request: Request):
        now = now_ms()
        if now < failures["until"]:
            raise Unauthorized("locked_out", "Too many attempts; wait and try again.",
                               retry_ms=int(failures["until"] - now))
        passphrase = str(parse_json(await request.body()).get("passphrase", ""))
        stored = await state.registry.read(lambda tx: tx.scalar("SELECT passphrase_hash FROM human_auth WHERE id = 1"))
        if not stored:
            raise Unavailable("setup_required", "Set a passphrase first: run `my-team setup` in a terminal.")
        if not await asyncio.to_thread(auth.verify_passphrase, passphrase, stored):
            failures["count"] += 1
            if failures["count"] >= 5:
                failures["until"] = now + min(2 ** (failures["count"] - 5) * 30_000, 15 * 60_000)
            raise Unauthorized("bad_passphrase", "Passphrase is incorrect.")
        failures.update(count=0, until=0.0)
        token, digest = auth.new_session_token()
        await state.registry.write(lambda tx: tx.execute(
            "INSERT INTO human_sessions (token_hash, created_ms, expires_ms) VALUES (?, ?, ?)",
            (digest, now, now + SESSION_TTL_MS)))
        response = envelope({"authenticated": True})
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_MS // 1000, httponly=True, samesite="strict",
                            path="/")
        return response

    @app.post("/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            digest, now = auth.token_hash(token), now_ms()
            await state.registry.write(lambda tx: tx.execute(
                "UPDATE human_sessions SET revoked_ms = ? WHERE token_hash = ?", (now, digest)))
        response = envelope({"authenticated": False})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.post("/api/v1/registry/{name}")
    async def registry_call(name: str, request: Request):
        op = REGISTRY_OPS.get(name)
        if op is None:
            raise NotFound("unknown_operation", f"No registry operation {name}.")
        body = await request.body()
        actor = await authenticate(request, body)
        if actor.kind not in op.actors:
            raise Forbidden("human_only" if op.human_only else "agent_only", f"{name} is not available to {actor.kind}s.")
        inp = parse(op.input, body)
        identity = None
        if hasattr(inp, "cwd"):
            cwd = Path(inp.cwd)
            if not cwd.is_dir():
                raise NotFound("unknown_directory", "cwd is not an existing directory.")
            identity = await asyncio.to_thread(projects.identify, cwd)
        data = await state.registry.write(lambda tx: op.run(RegistryCtx(tx, actor, now_ms(), identity), inp))
        if name == "project_init":
            await asyncio.to_thread(state.project_db, data["project"]["id"])
        return envelope(data)

    @app.post("/api/v1/projects/{project_id}/{name}")
    async def project_call(project_id: str, name: str, request: Request):
        op = OPS.get(name)
        if op is None:
            raise NotFound("unknown_operation", f"No operation {name}.")
        body = await request.body()
        actor = await authenticate(request, body)
        if actor.kind not in op.actors:
            raise Forbidden("human_only" if op.human_only else "agent_only", f"{name} is not available to {actor.kind}s.")
        inp = parse(op.input, body)
        project = await state.project(project_id)
        db = await asyncio.to_thread(state.project_db, project_id)

        def unit(tx):
            now = now_ms()
            session, current = None, actor
            if actor.is_agent and name not in AGENT_INFRA:
                if not actor.session_id:
                    raise Unauthorized("session_required", "Register the session first.")
                session = sessions.get(tx, actor.session_id)
                current = agent_actor(session)
                if name not in NOT_ACTIVITY:
                    sessions.touch(tx, session["id"], now)
            ctx = Ctx(tx, current, project, now, sessions.stall_cutoff(now, state.started_ms), state.started_ms, session)
            data = op.run(ctx, inp)
            meta = {"cursor": f"{db_epoch(tx)}.{events.latest_id(tx)}"}
            if session is not None and name not in NO_NOTICES:
                news = notices.collect(tx, session, include_pending=False)
                if notices.has_news(news):
                    meta["notices"] = news
            return data, meta

        data, meta = await db.write(unit)
        return envelope(data, meta=meta)

    @app.get("/api/v1/events/stream")
    async def stream(request: Request, cursors: str = ""):
        if await authenticate(request, b"") != HUMAN:
            raise Forbidden("human_only", "The event stream is for the dashboard.")
        wanted = {}
        for part in filter(None, cursors.split(",")):
            project_id, _, position = part.partition(":")
            epoch, _, after = position.partition(".")
            wanted[project_id] = (epoch, int(after) if after.isdigit() else 0)
        dbs = {pid: await asyncio.to_thread(state.project_db, pid) for pid in wanted}
        queue = state.broadcaster.subscribe(list(wanted))

        def frame(project_id: str, event: dict, epoch: str) -> str:
            data = json.dumps({"project": project_id, **event}, separators=(",", ":"))
            return f"id: {epoch}.{event['id']}\nevent: change\ndata: {data}\n\n"

        async def body():
            last: dict[str, int] = {}
            epochs: dict[str, str] = {}
            try:
                yield "retry: 2000\n\n"
                for project_id, (epoch, after) in wanted.items():
                    current_epoch = await dbs[project_id].read(db_epoch)
                    epochs[project_id] = current_epoch
                    if epoch != current_epoch:
                        last[project_id] = await dbs[project_id].read(events.latest_id)
                        yield f"event: reset\ndata: {json.dumps({'project': project_id})}\n\n"
                        continue
                    last[project_id] = after
                    while batch := await dbs[project_id].read(lambda tx, a=last[project_id]: events.after(tx, a)):
                        for event in batch:
                            yield frame(project_id, event, current_epoch)
                            last[project_id] = event["id"]
                while not await request.is_disconnected():
                    try:
                        project_id, batch = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    for event in batch:
                        if event["id"] > last.get(project_id, 0):
                            yield frame(project_id, event, epochs[project_id])
                            last[project_id] = event["id"]
            finally:
                state.broadcaster.unsubscribe(queue)

        return StreamingResponse(body(), media_type="text/event-stream",
                                 headers={"cache-control": "no-store", "x-accel-buffering": "no"})

    static = static_dir or Path(str(resources.files("my_team") / "static"))
    if (static / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            if path.startswith(("api/", "auth/")):
                raise NotFound("not_found", "No such endpoint.")
            candidate = (static / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(static.resolve()):
                return FileResponse(candidate)
            return FileResponse(static / "index.html", headers={"cache-control": "no-store"})

    @app.exception_handler(SchemaTooNew)
    async def schema_too_new(_request, exc: SchemaTooNew):
        return fail(Unavailable("schema_too_new", str(exc)))

    @app.exception_handler(Exception)
    async def unexpected(_request, exc: Exception):
        log.exception("request_failed", exc_info=exc)
        return envelope(error={"code": "internal_error", "message": "Unexpected server error.", "details": {}},
                        status=500)

    return Guard(app, state)


def parse_json(body: bytes) -> dict:
    try:
        value = json.loads(body or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}

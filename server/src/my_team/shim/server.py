import asyncio
import json
import os
import re
import uuid

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from my_team import __version__, activation
from my_team.client import DaemonError, DaemonUnavailable, connect
from my_team.context import NOT_INITIALIZED, render_context, unavailable
from my_team.hook import output as hook_output
from my_team.domain.sessions import HEARTBEAT_INTERVAL_S
from my_team.ops import OPS

INSTRUCTIONS = ("my-team coordinates this project's AI agents through shared tickets, notices and docs. Call "
                "team_context at session start, claim a ticket before implementation edits, and stop at in_review; "
                "only the human closes tickets.")
SINGLE_SESSION_AGENTS = {"claude-code", "hermes"}
HOOK_AGENTS = {"claude-code", "codex"}
INTERNAL = "Internal: invoked by my-team hooks. Do not call directly."

LOCAL_TOOLS = {
    "team_init": ("Bind this repository as a my-team project and turn my-team on for it. Ask the human first: it "
                  "writes .my-team/project.toml.",
                  {"name": {"type": "string", "description": "Project name; defaults to the folder name."},
                   "key": {"type": "string", "description": "Ticket key prefix such as MT; derived when omitted."}}),
    "team_active": ("Turn my-team on or off for a scope: global, directory, session, or one-time (with a task). "
                    "scope=status reports the current state without changing it.",
                    {"scope": {"type": "string", "enum": [*activation.SCOPES, "status"]},
                     "state": {"type": "string", "enum": ["on", "off"], "default": "on"},
                     "task": {"type": "string", "description": "What the one-time activation covers."},
                     "path": {"type": "string", "description": "Directory for the directory scope."}}),
    "hook_prompt": (INTERNAL, {"session_id": {"type": "string"}, "cwd": {"type": "string"},
                               "prompt": {"type": "string"}}),
    "hook_session_start": (INTERNAL, {"session_id": {"type": "string"}, "source": {"type": "string"}}),
}

PROMPTS = {
    "init": ("Initialize this repository as a my-team project.",
             None,
             "Follow the my-team skill for `init`: confirm with the human, then call team_init."),
    "active": ("Turn my-team on or off for a scope, or show status.",
               [{"name": "args", "description": "Scope and state, e.g. `session`, `directory off`.",
                 "required": False}],
               "Follow the my-team skill for `active`: call team_active with the parsed arguments."),
    "audit": ("Audit this project without modifying files.",
              [{"name": "args", "description": "Audit scope, e.g. `all`, `docs`, `code`.", "required": False}],
              "Follow the my-team skill for `audit`: audit arrives in slice S4."),
}


class NotInitialized(Exception):
    pass


def agent_type_of(client_name: str) -> str:
    name = (client_name or "").lower()
    for marker, agent_type in (("claude", "claude-code"), ("codex", "codex"), ("opencode", "opencode"),
                               ("hermes", "hermes")):
        if marker in name:
            return agent_type
    return re.sub(r"[^a-z0-9-]", "-", name).strip("-")[:30] or "agent"


class Shim:
    def __init__(self):
        self.cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        self.agent_type = None
        self.client = None
        self.project = None
        self.sessions: dict[str, dict] = {}
        self.primary: str | None = None
        self.fallback_id = f"shim-{uuid.uuid4()}"
        self.lock = asyncio.Lock()
        self.heartbeat: asyncio.Task | None = None
        self.warned: set[str] = set()

    def identify(self, ctx) -> None:
        if self.agent_type is None:
            params = ctx.session.client_params
            self.agent_type = agent_type_of(params.client_info.name if params else "")

    def tools(self) -> list[types.Tool]:
        listed = [types.Tool(name=op.name, description=op.description, input_schema=op.input.model_json_schema(),
                             annotations=types.ToolAnnotations(read_only_hint=op.read_only))
                  for op in OPS.values() if op.tool]
        for name, (description, properties) in LOCAL_TOOLS.items():
            if name.startswith("hook_") and self.agent_type not in HOOK_AGENTS:
                continue
            listed.append(types.Tool(name=name, description=description,
                                     input_schema={"type": "object", "properties": properties}))
        return listed

    def prompts(self) -> list[types.Prompt]:
        if self.agent_type == "claude-code":
            return []
        return [types.Prompt(name=name, description=description,
                             arguments=[types.PromptArgument(name=a["name"], description=a.get("description"),
                                                             required=a.get("required"))
                                        for a in args] if args else None)
                for name, (description, args, _) in PROMPTS.items()]

    def prompt_text(self, name: str, args: dict | None) -> str:
        _, _, text = PROMPTS[name]
        given = (args or {}).get("args")
        return f"{text} Arguments: {given}" if given else text

    def native_id(self, meta, explicit: str | None = None) -> str:
        if explicit:
            return explicit
        if self.agent_type == "claude-code" and os.environ.get("CLAUDE_CODE_SESSION_ID"):
            return self.primary or os.environ["CLAUDE_CODE_SESSION_ID"]
        for key in ("threadId", "sessionID"):
            if meta and meta.get(key):
                return str(meta[key])
        return self.primary or self.fallback_id

    async def _connected(self) -> dict:
        if self.client is None:
            self.client = await asyncio.to_thread(connect)
        if self.project is None:
            found = await asyncio.to_thread(self.client.registry, "project_resolve", {"cwd": self.cwd})
            if not found["project"]:
                raise NotInitialized()
            self.project = found["project"]
        return self.project

    async def session(self, native: str) -> dict:
        async with self.lock:
            if native in self.sessions:
                return self.sessions[native]
            project = await self._connected()
            single = self.agent_type in SINGLE_SESSION_AGENTS
            if single and self.primary in self.sessions and native != self.primary:
                previous = self.sessions.pop(self.primary)
                info = await asyncio.to_thread(self.client.project, project["id"], "session_succeed",
                                               {"new_native_id": native}, previous["session_id"])
            else:
                info = await asyncio.to_thread(self.client.project, project["id"], "session_register", {
                    "agent_type": self.agent_type, "native_id": native, "root_path": self.cwd,
                    "agent_name": os.environ.get("MY_TEAM_AGENT") or None})
            self.sessions[native] = info
            if single:
                self.primary = native
            if self.heartbeat is None:
                self.heartbeat = asyncio.create_task(self._beat())
            return info

    async def op(self, native: str, name: str, args: dict) -> tuple[dict, dict]:
        for attempt in (1, 2):
            info = await self.session(native)
            try:
                return await asyncio.to_thread(self.client.project_with_meta, self.project["id"], name, args,
                                               info["session_id"])
            except DaemonError as exc:
                if exc.code != "unknown_session" or attempt == 2:
                    raise
                self.sessions.pop(native, None)
        raise AssertionError("unreachable")

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            for native, info in list(self.sessions.items()):
                try:
                    await asyncio.to_thread(self.client.project, self.project["id"], "session_heartbeat", {},
                                            info["session_id"])
                except DaemonError:
                    self.sessions.pop(native, None)
                except DaemonUnavailable:
                    try:
                        self.client = await asyncio.to_thread(connect)
                    except DaemonUnavailable:
                        pass

    async def close(self) -> None:
        if self.heartbeat:
            self.heartbeat.cancel()
        for info in self.sessions.values():
            try:
                await asyncio.to_thread(self.client.project, self.project["id"], "session_end", {}, info["session_id"])
            except (DaemonError, DaemonUnavailable):
                pass

    async def call(self, name: str, args: dict, meta) -> types.CallToolResult:
        explicit = args.pop("session", None)
        try:
            if name in LOCAL_TOOLS:
                text = await getattr(self, name)(args, meta, explicit)
            elif name in OPS and OPS[name].tool:
                data, reply_meta = await self.op(self.native_id(meta, explicit), name, args)
                text = json.dumps(data, indent=1)
                if reply_meta.get("notices"):
                    text += "\n\nNotices: " + json.dumps(reply_meta["notices"])
            else:
                return _error(f"Unknown tool {name}.")
        except NotInitialized:
            return _error("This repository is not a my-team project yet. Ask the human, then run /my-team:init.")
        except DaemonUnavailable as exc:
            return _error(str(exc))
        except DaemonError as exc:
            return _error(json.dumps(exc.to_dict()))
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    async def team_init(self, args: dict, meta, explicit) -> str:
        if self.client is None:
            self.client = await asyncio.to_thread(connect)
        payload = {"cwd": self.cwd} | {k: args[k] for k in ("name", "key") if args.get(k)}
        project = (await asyncio.to_thread(self.client.registry, "project_init", payload))["project"]
        self.project = project
        activation.set_scope("directory", True, cwd=project["root"])
        verb = "created" if project["created"] else "was already initialized"
        return (f"Project {project['name']} ({project['key']}) {verb} at {project['root']}. my-team is now active "
                "for this directory. Open the dashboard with `my-team open` to create and assign tickets.")

    async def team_active(self, args: dict, meta, explicit) -> str:
        native = self.native_id(meta, explicit)
        if args.get("scope") == "status":
            return json.dumps(activation.resolve(activation.load(), self.agent_type, native, self.cwd))
        on = args.get("state", "on") != "off"
        activation.set_scope(args["scope"], on, cwd=self.cwd, agent_type=self.agent_type, native_id=native,
                             task=args.get("task"), path=args.get("path"))
        return json.dumps(activation.resolve(activation.load(), self.agent_type, native, self.cwd))

    async def _hook(self, event: str, native: str, cwd: str | None, full: bool | None) -> str:
        resolved = activation.resolve(activation.load(), self.agent_type, native, cwd or self.cwd)
        if not resolved["active"]:
            return "{}"
        try:
            info = await self.session(native)
            first = full if full is not None else not info.get("context_sent")
            data, _ = await self.op(native, "team_context", {"delta": not first})
            info["context_sent"] = True
            return hook_output(self.agent_type, event, render_context(data, resolved, first))
        except NotInitialized:
            return hook_output(self.agent_type, event, self._once("init", NOT_INITIALIZED))
        except (DaemonUnavailable, DaemonError) as exc:
            return hook_output(self.agent_type, event, self._once("down", unavailable(exc)))

    def _once(self, key: str, text: str) -> str | None:
        if key in self.warned:
            return None
        self.warned.add(key)
        return text

    async def hook_prompt(self, args: dict, meta, explicit) -> str:
        return await self._hook("prompt", args.get("session_id") or self.native_id(meta), args.get("cwd"),
                                None)

    async def hook_session_start(self, args: dict, meta, explicit) -> str:
        return await self._hook("session-start", args.get("session_id") or self.native_id(meta), None, True)


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def build(shim: Shim) -> Server:
    async def list_tools(ctx, params):
        shim.identify(ctx)
        return types.ListToolsResult(tools=shim.tools())

    async def call_tool(ctx, params):
        shim.identify(ctx)
        return await shim.call(params.name, dict(params.arguments or {}), params.meta)

    async def list_prompts(ctx, params):
        shim.identify(ctx)
        return types.ListPromptsResult(prompts=shim.prompts())

    async def get_prompt(ctx, params):
        shim.identify(ctx)
        if shim.agent_type == "claude-code" or params.name not in PROMPTS:
            raise ValueError(f"Unknown prompt {params.name}.")
        return types.GetPromptResult(
            messages=[types.PromptMessage(role="user", content=types.TextContent(
                type="text", text=shim.prompt_text(params.name, params.arguments)))])
    return Server("my-team", version=__version__, instructions=INSTRUCTIONS, on_list_tools=list_tools,
                  on_call_tool=call_tool, on_list_prompts=list_prompts, on_get_prompt=get_prompt)


def main() -> int:
    shim = Shim()
    server = build(shim)

    async def run():
        async with stdio_server() as (read, write):
            try:
                await server.run(read, write, server.create_initialization_options())
            finally:
                await shim.close()

    asyncio.run(run())
    return 0

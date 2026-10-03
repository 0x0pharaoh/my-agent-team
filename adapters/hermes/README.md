# Hermes Adapter for my-team — Research Findings and Bridge Design

## 1. Findings (with citations)

All citations are against Hermes Agent v0.21.4 source at `%LOCALAPPDATA%\hermes\hermes-agent\`.

### 1.1 Kanban (U3)

**Data model.** Hermes kanban is a separate SQLite system (`kanban.db`), independent of my-team.
The `tasks` table schema is in `hermes_cli/kanban_db.py:SCHEMA_SQL` (lines 440–620). Key columns:

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `t_<4-hex-bytes>` (line 559–562) |
| `title` | TEXT NOT NULL | |
| `body` | TEXT | Optional description |
| `assignee` | TEXT | Profile name (lowercase, normalised) |
| `status` | TEXT NOT NULL | One of `VALID_STATUSES` (line 44): `triage`, `todo`, `scheduled`, `ready`, `running`, `blocked`, `review`, `done`, `archived` |
| `priority` | INTEGER DEFAULT 0 | |
| `created_by` | TEXT | |
| `created_at` | INTEGER NOT NULL | Unix seconds |
| `session_id` | TEXT | Originating `HERMES_SESSION_ID` (line 410) |
| `claim_lock` | TEXT | `host:pid` of claimer (line 565–572) |
| `claim_expires` | INTEGER | Claim TTL — default 15 min (line 130) |
| `block_kind` | TEXT | `VALID_BLOCK_KINDS` (line 48): `dependency`, `needs_input`, `capability`, `transient` |
| `block_recurrences` | INTEGER | Unblock-loop counter (line 420) |
| `worker_pid` / `worker_started_at` | INTEGER | Spawned worker identity (line 424, 434) |

**Task lifecycle (kanban_db.py):**

- **Create:** `create_task()` at line 1012. Params: `title`, `body`, `assignee`, `created_by`, `workspace_kind` (`scratch`/`worktree`/`dir`), `initial_status` (`running` or `blocked` only — line 1052–1053), `parents` (dependency ids), `session_id`, `idempotency_key`, plus model/reasoning overrides. Returns task id. Fires `created` event.
- **Claim:** `claim_task()` at line 1829. Atomic `ready → running` CAS. Returns claimed `Task` or `None` (already claimed / not ready / parents unsatisfied). Sets `claim_lock`, `claim_expires`, opens a `task_runs` row. Fires `kanban_task_claimed` hook AFTER commit.
- **Complete:** `complete_task()` at line 2195. `running|ready|blocked|review → done`. Requires `expected_run_id` or `force=True` for live claims. Records `result`, `summary`. Fires `kanban_task_completed` hook AFTER commit.
- **Block:** `block_task()` at line 2500. `running|ready → blocked` (or `todo` for `dependency` kind, or `triage` after `BLOCK_RECURRENCE_LIMIT`=2 recurrences). Clears claim. Fires `kanban_task_blocked` hook AFTER commit.

**Hooks — kanban_task_*.** These are in `VALID_HOOKS` (plugins.py:162):
```python
"kanban_task_claimed", "kanban_task_completed", "kanban_task_blocked",
```
Payload fields (from `_fire_task_hook` at kanban_db.py:130–135 and the individual fire sites):
- `kanban_task_claimed` (line 1839): `task_id`, `board`, `assignee`, `run_id`
- `kanban_task_completed` (line 2270): `task_id`, `board`, `assignee`, `run_id`, `summary`
- `kanban_task_blocked` (line 2560/2562): `task_id`, `board`, `assignee`, `run_id`, `reason`

**Can a plugin write tasks in-process?** The kanban DB functions (`create_task`, `claim_task`, etc.) are plain Python in `kanban_db.py` and importable by any code. However, the my-team bridge design (plan §5.8) calls `my-team call <op>` through the CLI launcher — a shell-out, not an in-process import. This is by design: the daemon never runs Hermes, and the bridge plugin calls the CLI. The `plugins/kanban/` directory (dashboard + systemd sub-plugins) does not expose a kanban write API to other plugins.

**Can kanban_task_* hooks veto?** No. Per `plugins.py:158–162`: "Kanban task observers … fired AFTER the DB commit so a slow plugin never holds the SQLite write lock; returns ignored." Hook return values are discarded.

**CLI surface.** `hermes kanban create`, `hermes kanban claim <id>`, `hermes kanban complete <id>`, `hermes kanban block <id>`, `hermes kanban unblock <id>`. These are the primitives the bridge uses.

### 1.2 Session identity

**HERMES_SESSION_ID origin.** `agent_init.py:1127–1143` (`_publish_session_id`):
```python
def _publish_session_id(session_id: str) -> None:
    try:
        from gateway.session_context import set_current_session_id
        set_current_session_id(session_id)
    except Exception:
        if not is_delegated_child_context():
            os.environ["HERMES_SESSION_ID"] = session_id
```
The session id is minted by `new_session_id()` from `hermes_state_ids.py` (called at `agent_init.py:1149`). It is a ULID-like string.

**parent_session_id.** Set at `agent_init.py:1166`:
```python
agent._parent_session_id = parent_session_id
```
Passed into `run_agent()` / `AIAgent.__init__` (line 2341). For compression, the child session gets the parent's id.

**Compression creates child sessions.** `conversation_compression.py` imports `new_session_id` as `mint_session_id` (line 37). When compression fires, a new session id is minted and the old session's state is carried over. The memory provider's `on_session_switch` hook (memory_provider.py:168–174) fires with `parent_session_id` set:
```python
def on_session_switch(self, new_session_id, *, parent_session_id="", reset=False, rewound=False, **kwargs):
    """session_id reassigned mid-process (/resume, /branch, /reset, /new, compression)"""
```
This is the mechanism my-team's bridge uses to track lineage.

**Which hook events see session_id?** All of them. From `hermes_cli/hooks.py:_DEFAULT_PAYLOADS` (lines 107–135), every event carries `session_id`:

```python
"session_id": kwargs.get("session_id") or kwargs.get("parent_session_id") or "",
```

And from the default payload definitions:
- `on_session_start`: `{"session_id": "test-session"}` (line 126)
- `pre_llm_call`: `{"session_id": "test-session", "user_message": ..., "conversation_history": [], ...}` (lines 116–119)
- `pre_tool_call`: `{"session_id": "test-session", "tool_name": ..., "args": {...}}` (lines 107–110)

### 1.3 Hooks

**config.yaml syntax for shell hooks** (defined by the `hooks:` key in config.yaml; schema and examples at `config_defaults.py:1699–1707` and `website/docs/user-guide/features/hooks.md`):

```yaml
hooks:
  on_session_start:
    - command: "python -I -S /path/to/hook.py session-start"
      timeout: 30
      fail_closed: false
  pre_llm_call:
    - command: "python -I -S /path/to/hook.py pre-llm"
      timeout: 30
      fail_closed: false
  pre_tool_call:
    - matcher: "my-tool"
      command: "python -I -S /path/to/hook.py pre-tool"
      timeout: 30
      fail_closed: true
```

- `timeout` (optional, default 30s): how long the hook may run before Hermes kills it.
- `fail_closed` (optional, default false): when `true`, a hook failure (non-zero exit, timeout, crash) blocks the event; when `false`, failure is logged but the event proceeds.
- `pre_tool_call` also accepts a `matcher` (regex) to scope the hook to specific tools. If `matcher` is absent, the hook fires for all tools.

**pre_llm_call returning {"context"}.** From `plugins_dispatch.py:216`: "``pre_llm_call`` may return ``{"context": "..."}`` (or a str) to inject." This context is injected into the system prompt before the LLM call. The shell-hook parser for `pre_llm_call` (agent/shell_hooks.py:469–474, `_parse_context`) accepts either a JSON `{"context": "..."}` or a bare string on stdout.

**pre_tool_call block/approve.** From `agent/shell_hooks.py:469–474` (`_parse_response` + `_parse_pre_tool_call`): the hook receives `{"tool_name": ..., "args": {...}, "description": ..., "command": ...}`. A non-zero exit with a JSON body `{"action": "block", "reason": "..."}` blocks the tool call. An exit-0 `{"action": "approve"}` or empty body approves. `pre_tool_call` is the only shell hook that can block (plugins.py:143–147 confirms this is the approval hook).

**Consent flow (shell hooks).** From `agent/shell_hooks.py:163–265` (the `run_once` / `_evaluate_result` / allowlist machinery) and `hermes_cli/hooks.py`:

- On first use of a new (event, command) pair, the hook command is NOT executed; Hermes presents the user with a TTY consent prompt.
- The user can approve once (for this invocation), for the session, or always (add to allowlist).
- The allowlist is stored in `~/.hermes/shell-hooks-allowlist.json` (loaded by `shell_hooks.load_allowlist()`).
- `hooks_auto_accept` (config_defaults.py:1707) or `--accept-hooks` / `HERMES_ACCEPT_HOOKS=1` skips the prompt entirely — used in CI / non-interactive runs.

**Plugin register_hook equivalents.** From `plugins.py:912–914`:
```python
def register_hook(self, hook_name: str, callback: Callable) -> PluginRegistration:
    """Register a lifecycle hook callback (unknown names warn but are still stored)."""
    return self._track_callback("hook", hook_name, callback, self._manager._hooks, VALID_HOOKS)
```
- Plugins call `ctx.register_hook("on_session_start", my_callback)` during `register(ctx)`.
- `VALID_HOOKS` (plugins.py:108–178) lists all known hook names. Unknown names warn but are stored.
- Hook callbacks receive `**kwargs` — the payload is hook-specific.
- `pre_tool_call` callbacks can return `{"action": "block", "reason": "..."}` or `{"action": "approve"}` to control tool execution (same semantics as shell hooks).
- `pre_llm_call` callbacks can return `{"context": "..."}` to inject into the system prompt.
- Kanban task hooks (`kanban_task_claimed`, etc.) are observer-only; return values are ignored (plugins.py:158–162).

### 1.4 MCP

**mcp_servers entry for stdio server with env vars** (`config_defaults.py:1558–1571`):
```yaml
mcp_servers:
  my-team:
    command: "/path/to/my-team.exe"
    args: ["mcp"]
    env:
      MY_TEAM_AGENT: "hermes"
      MY_TEAM_HOME: "${MY_TEAM_HOME}"
      MY_TEAM_PORT: "${MY_TEAM_PORT}"
```

- `command` can be a string (shell-resolved) or a list (argv). Hermes recommends a list for paths with spaces.
- `env` is a dict of string values. `${VAR}` references expand to environment variables at startup.
- `lazy: true` (optional): do not spawn the server until a tool is called. Default is `false` (spawn on Hermes start).
- Env vars set here are passed to the stdio server process.

**Tool naming: mcp_<server>_<tool>.** Confirmed at `mcp_tool_schema.py:159–175`:
```python
MCP_TOOL_NAME_PREFIX = "mcp__"

def mcp_prefixed_tool_name(server_name: str, tool_name: str) -> str:
    full_name = f"{MCP_TOOL_NAME_PREFIX}{sanitize_mcp_name_component(server_name)}__{sanitize_mcp_name_component(tool_name)}"
    ...
    return full_name
```
- Tools from `mcp_servers.my-team` appear as `mcp__my-team__<tool_name>`.
- If the full name exceeds 64 chars, a SHA-256 hash suffix is appended.
- The double underscore `__` separates server name from tool name; single underscores in server/tool names are preserved.

### 1.5 Skills

**Install path and slug.** From `agent_import.py:491` and `config.yaml` `skills.external_dirs` (lines 1100–1105):
- Skills live in `$HERMES_HOME/skills/<category>/<skill-slug>/`.
- The slug is the directory name, derived from the skill's frontmatter `name` field by lowercasing and replacing spaces with hyphens.
- For a skill named "my-team", the slug is `my-team`, installed at `$HERMES_HOME/skills/software-development/my-team/` (category `software-development` per the plan).

**Does `/my-team init` work?** Yes. Hermes strips `:` from the skill slug for command routing (confirmed at `agent_plugins.py:208`). The skill's `SKILL.md` must define the slash command. For a skill at `skills/software-development/my-team/`, the command is `/my-team init` (the `:` in the qualified name `software-development:my-team` is dropped).

**Skill discovery.** Hermes scans:
1. `$HERMES_HOME/skills/` (built-in skills)
2. `~/.agents/skills/` (project-local skills, requires `hermes skills trust`)
3. Any directory in `config.yaml` `skills.external_dirs`

### 1.6 Memory provider interface

**Method signatures** (`memory_provider.py:84–201`):
```python
class MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None: ...

    def unavailable_reason(self) -> str: ...  # default ""

    def system_prompt_block(self) -> str: ...  # default ""

    def prefetch(self, query: str, *, session_id: str = "") -> str: ...  # default ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None: ...  # default no-op

    def recall_status(self) -> Optional[RecallStatus]: ...  # default None

    def sync_turn(self, user_content, assistant_content, *, session_id="", messages=None, turn_author=None) -> None: ...  # default no-op

    @abstractmethod
    def get_tool_schemas(self) -> List[Dict[str, Any]]: ...

    def handle_tool_call(self, tool_name, args, **kwargs) -> str: ...  # default NotImplementedError

    def shutdown(self) -> None: ...  # default no-op

    # Optional hooks:
    def on_turn_start(self, turn_number, message, **kwargs) -> None: ...
    def identity_signature(self) -> Dict[str, Any]: ...
    def on_session_end(self, messages: List[Dict[str, Any]]) -> None: ...
    def on_session_switch(self, new_session_id, *, parent_session_id="", reset=False, rewound=False, **kwargs) -> None: ...
    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str: ...
    def on_delegation(self, task, result, *, child_session_id="", **kwargs) -> None: ...
    def get_config_schema(self) -> List[Dict[str, Any]]: ...
    def save_config(self, values, hermes_home) -> None: ...
    def on_memory_write(self, action, target, content, metadata=None) -> None: ...
    def backup_paths(self) -> List[str]: ...
```

**How to activate one.** From `memory_manager.py:3–4`:
- Plugins ship in `plugins/memory/<name>/`, activated via `memory.provider` config key.
- Only ONE external provider can be active at a time.
- Set `memory.provider: <provider-name>` in `config.yaml`.
- The provider is loaded by the plugin manager, then activated by `MemoryManager`.
- Lifecycle: `initialize()` → per-turn `system_prompt_block()` + `prefetch()` + `sync_turn()` → `shutdown()` on session end.

**Conflicts with an already-active provider.** From `memory_manager.py`:
- If `memory.provider` is changed while a provider is active, the old provider's `shutdown()` is called before the new one is initialized.
- Only one external provider can be active. If a plugin tries to register a second provider, it replaces the first (last-writer-wins).
- The built-in provider (`"default"`, `"builtin"`, `""`) is always available and takes precedence when `memory.provider` is set to one of its sentinel values.

---

## 2. Bridge Design

### 2.1 Field ownership

Hermes owns the kanban task record (id, status, assignee, claim_lock, block_kind, etc.).
my-team owns the ticket record (id, title, status, assignee, claims, timeline).

The bridge never duplicates state — each side is authoritative for its own records.

### 2.2 Mapping table fields

The bridge maintains a mapping table (in my-team's SQLite DB) with these fields:

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | Auto-generated |
| `my_team_ticket_id` | TEXT NOT NULL | FK to my-team tickets |
| `hermes_task_id` | TEXT NOT NULL | FK to Hermes kanban tasks |
| `hermes_session_id` | TEXT | The Hermes session that created/claimed the task |
| `hermes_board` | TEXT | Hermes kanban board name (default: "default") |
| `last_synced_hash` | TEXT | Hash of the last-synced my-team ticket state |
| `direction` | TEXT NOT NULL | `my-team→hermes` or `hermes→my-team` |
| `created_at` | INTEGER NOT NULL | Unix seconds |
| `updated_at` | INTEGER NOT NULL | Unix seconds |

### 2.3 Echo-loop prevention via last_synced_hash

When the bridge syncs a ticket from my-team to Hermes, it computes a hash of the ticket's relevant fields (status, assignee, title, body) and stores it in `last_synced_hash`.

On the next sync pass:
1. Fetch the my-team ticket.
2. Compute the current hash.
3. Compare with `last_synced_hash`.
4. If identical → skip (no update needed).
5. If different → update the Hermes task and update `last_synced_hash`.

This prevents:
- Re-syncing the same state when nothing changed.
- Echo loops where my-team updates a ticket based on a Hermes sync, which triggers another sync back.

Hashes are computed over a deterministic JSON serialization of the fields, sorted by key.

### 2.4 Direction of every event

**my-team → Hermes (pull, initiated by Hermes bridge plugin):**

| my-team event | Hermes action | Notes |
|---|---|---|
| `ticket.create` | Create Hermes kanban task | Initial status: `triage` (not `running`) |
| `ticket.status → in_progress` | Update Hermes task `running` | Only if Hermes seat is assignee |
| `ticket.status → in_review` | Update Hermes task `review` | |
| `ticket.status → blocked` | Update Hermes task `blocked`, set `block_kind` | |
| `ticket.status → done` | Update Hermes task `done` | Human-only transition (D15) |
| `ticket.status → cancelled` | Delete or archive Hermes task | |
| `ticket.assignee → Hermes seat` | Update Hermes task `assignee` | |
| `ticket_claim` by Hermes session | (already covered by claim hook) | |

**Hermes → my-team (push, initiated by Hermes bridge plugin):**

| Hermes event | my-team action | Notes |
|---|---|---|
| `kanban_task_claimed` (Hermes seat) | `ticket_claim` for that session | If 409 → mark Hermes task blocked "claimed elsewhere in my-team" |
| `kanban_task_completed` | `ticket_update` → `in_review` | NEVER `done` (D15) |
| `kanban_task_blocked` | `ticket_update` → `blocked`, with note | |
| Task created in Hermes kanban | Create `proposed` ticket in my-team | Human must accept (D15) |

### 2.5 Behaviour while Hermes is not running

- The bridge plugin's `on_session_start` and `on_kanban_dispatch_tick` hooks fire only while Hermes is running.
- While Hermes is down, my-team continues to operate normally — tickets are created, claimed, completed by other agents.
- When Hermes starts again, the bridge plugin's `on_session_start` hook pulls event deltas from my-team's SSE/event log starting from the stored cursor, and reconciles all projected tasks.
- The bridge **does not queue events** for delivery when Hermes is offline. If a my-team ticket is claimed by a Hermes seat while Hermes is down, the claim succeeds in my-team but the Hermes task is not updated until the next sync. The UI shows the correct state (my-team is authoritative).
- If a Hermes user creates a task in the kanban board while Hermes is running but my-team is down, the task is created in Hermes kanban. When my-team comes back up, the bridge plugin (on its next `on_kanban_dispatch_tick`) sees the new task and creates a `proposed` ticket in my-team.

### 2.6 Bridge plugin location

The bridge plugin lives in `$HERMES_HOME/plugins/my-team-kanban/` (a Hermes plugin) and:
1. Registers `on_session_start` and `on_kanban_dispatch_tick` hooks
2. On `on_session_start`: pulls event deltas from my-team, reconciles projected tasks
3. On `on_kanban_dispatch_tick`: checks for new Hermes-created tasks, pushes to my-team as `proposed`
4. Calls `my-team call <op>` via subprocess for all my-team mutations (no auth code duplication)

---

## 3. Hermes column for plan §3.5

Each row from the adapter matrix (plan §3.5, Hermes 0.21.4 column), marked verified or unverified.

| Row | Claim | Status | Evidence |
|---|---|---|---|
| Install | `my-team install hermes` copies skill to `$HERMES_HOME/skills/software-development/my-team/` and adds `mcp_servers.my-team`, shell `hooks:` and bridge plugin to `config.yaml` | **Verified** | Plan §3.5; `my-team install` code not yet written (S5) — the *target* is verified against Hermes config schema |
| Skill discovery | `$HERMES_HOME/skills` scanned; project `.agents/skills` would need `hermes skills trust` so not used | **Verified** | `agent_import.py:491`; `config.yaml` `skills.external_dirs` (line 1103) confirms `~/.agents/skills` pattern |
| Commands | `/my-team init` / `/my-team audit` / `/my-team active <scope>` (skill slug; `:` stripped) | **Verified** | Plan §3.5; Hermes skill routing strips `:` from slug — confirmed by skill frontmatter parsing in `agent_plugins.py:208` |
| Context injection | Shell hook `pre_llm_call` returns `{"context"}`, plus `on_session_start`. Needs first-use consent. | **Verified** | `plugins_dispatch.py:216` confirms `pre_llm_call` returns context; `shell_hooks.py:163–166` confirms consent flow |
| Pre-edit guard (D18) | `pre_tool_call` on file tools → `{"action": "approve"}` | **Verified** | `shell_hooks.py:41–44` confirms `pre_tool_call` exit-2 blocking; `plugins.py:143–147` confirms `pre_tool_call` is the approval hook |
| MCP config | `mcp_servers.my-team` in `config.yaml` | **Verified** | `config_defaults.py:1558–1571` shows the stdio server stanza format; tool naming `mcp__server__tool` confirmed at `mcp_tool_schema.py:159–175` |
| Session id | Not passed to MCP. Tools take `session`; Hermes reference passes `${HERMES_SESSION_ID}`; hooks register it | **Verified** | `agent_init.py:1143` sets `HERMES_SESSION_ID`; `shell_hooks.py:89` includes it in hook payload |
| Lineage | Compression creates a child session → `succeed` | **Verified** | `conversation_compression.py:37` imports `new_session_id`; `memory_provider.py:168–174` `on_session_switch` receives `parent_session_id` |
| Live push (deferred, D13) | Plugin `inject_message` | **Unverified** | D13 is deferred; `inject_message` is mentioned in plan §3.5 but not found in Hermes 0.21.4 source — may be a later feature |
| Activation enforcement | Enforced after hook consent | **Verified** | `shell_hooks.py:163–166` consent flow; `hooks_auto_accept` (config_defaults.py:1707) controls auto-accept |
| Limits to document | Fast-moving (minimum version pinned); only one external memory provider can be active | **Verified** | `memory_manager.py:3–4` confirms one external provider limit; `config_defaults.py` pins model versions via `providers` config |

---

## 4. Open questions

1. **U3 (plan §1.3):** Does the bridge write kanban tasks via the in-process plugin API or shell out to `hermes kanban …`? The plan says "U3 decides whether writes go through the in-process API or the `hermes kanban` CLI." Current leaning: shell out to `hermes kanban create/claim/complete/block` via subprocess from the bridge plugin, because the kanban DB functions are importable but the my-team bridge design calls `my-team call <op>` from Hermes side. **Needs S5 spike.**

2. **Live push (D13):** Hermes 0.21.4 may not have `inject_message` yet. Deferred to later slice. **Unverified.**

3. **Bridge plugin storage.** Where does the bridge plugin store its cursor and `last_synced_hash`? The plan says "cursor stored in the plugin's storage" (§5.8). Hermes plugin state is via `PluginState` (plugins.py:286–288) — a profile-scoped JSON store. **Needs confirmation that `PluginState` is available to the bridge plugin.**

4. **Hermes kanban board slug.** The bridge targets which board? Default board (`kanban.db` at Hermes root) or a named board? my-team likely creates tasks on the default board. **Needs design decision.**

5. **my-team daemon HMAC auth from Hermes plugin.** The bridge plugin calls `my-team call <op>` as a subprocess. Does the CLI inherit the daemon's auth token from the environment, or does it need to authenticate separately? The daemon uses HMAC-signed requests (plan §5.2). **Needs verification that `my-team call` works without a separate login when the daemon is already running.**

---

*Generated from Hermes Agent v0.21.4 source analysis. All citations are file:line references into `%LOCALAPPDATA%\hermes\hermes-agent\`.*

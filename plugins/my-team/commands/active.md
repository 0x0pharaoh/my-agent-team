---
description: Turn my-team on or off (global, directory, session, one-time) or show its status
argument-hint: "global | directory [path] | session | one-time \"task\" | off <scope> | status"
disable-model-invocation: true
---
Change my-team activation from `$ARGUMENTS` by calling the `team_active` tool once:

- `status` or no arguments → `scope: "status"`.
- `off <scope>` → that scope with `state: "off"`.
- `global`, `directory [path]`, `session` → that scope with `state: "on"` (and `path` for directory).
- `one-time "task"` → `scope: "one-time"`, `state: "on"`, `task` set to the quoted text.

Then report in one line which scope is now in effect for this session.

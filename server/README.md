# my-team-agents

Local memory, messaging, and tickets for supervised multi-agent development. One `my-team` command runs the
loopback daemon (FastAPI + SQLite), the MCP shim for agents, hook handlers, and installers for Claude Code,
Codex, OpenCode, and Hermes, plus the human dashboard.

```bash
uv tool install my-team-agents
my-team install <agent>   # claude-code | codex | opencode | hermes (asks first)
my-team setup             # dashboard passphrase
my-team open              # dashboard
my-team doctor            # check the install, data and git
```

Full docs and source: https://github.com/0x0pharaoh/my-agent-team

# my-team

A cross-agent engineering skill with a local memory, messaging, and ticket platform. One human supervises many AI
coding sessions (Claude Code, Codex, OpenCode, Hermes) that share project memory, coordinate through tickets and
messages, and follow the same rules: minimal comments, strict DRY, a requirement review on every prompt, and five
authoritative project documents.

**Status: 0.1.0 pre-release.** OpenCode is verified live end to end; Claude Code, Codex, and Hermes are
unit-tested only, with live verification still pending.

## Use it (Windows/macOS/Linux)

```bash
uv tool install my-team-agents                 # release (or `uv tool install --editable ./server` for dev)
my-team install <agent>                        # claude-code | codex | opencode | hermes (asks first)
my-team setup                                  # dashboard passphrase
```

In any project in Claude Code, run `/my-team:init`. It binds the project, switches my-team on, drafts the five docs
with you, and proposes tickets. Run `my-team open` for the dashboard, where you create, assign and close tickets.
Agents claim what you assign and stop at review. The pre-edit permission prompt works in Claude Code, OpenCode, and
Hermes; Codex sessions follow the claim rule unguarded. Opt-in hard block (spike): set
`MY_TEAM_CODEX_DENY_UNCLAIMED=1` where Codex runs and reinstall, to deny unclaimed `apply_patch` edits.

The design is in [`docs/`](docs/):

| Document | Holds |
|---|---|
| [PRD](docs/PRD.md) | What my-team is for, who uses it, and what it must do |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Components, data flow, tech stack, agent adapters |
| [RULES](docs/RULES.md) | How this repository is built, by humans and agents alike |
| [DESIGN](docs/DESIGN.md) | The local dashboard's visual system |
| [SECURITY](docs/SECURITY.md) | Trust boundaries, what is and is not guaranteed, mandatory rules |

## License

MIT

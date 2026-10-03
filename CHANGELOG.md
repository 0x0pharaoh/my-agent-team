# Changelog

## Unreleased

- WebAuthn passkeys: add/remove in Settings, log in with a security key; passphrase still works.
- Memory purge (human-only overwrite + vacuum + backup flags) and database restore with integrity,
  schema and trigger checks; purge button and restore section in the dashboard.

## 0.1.0

- Daemon (loopback, HMAC-signed agents, passphrase login, CSP), tickets with claim fencing and human-only
  closing, sprints, agent seats and sessions with lineage, SSE; MCP shim with init/active/audit prompts.
- Memory, messages, and questions with redaction; pre-edit guard; daily SQLite backups; `my-team doctor`.
- Dashboard: Board, Backlog, Inbox, Memory, Agents, Docs, Repo.
- Installers and adapters for Claude Code, Codex, OpenCode, and Hermes (kanban bridge deferred).
- Playwright E2E (ticket flow, sprint lifecycle); wheel ships the built UI plus skills and adapters.

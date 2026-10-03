# my-team — Security

| Field | Value |
|---|---|
| Version | 1.0 |
| Last updated | 2026-09-23 |
| Status | Approved |

This document describes intended controls; it does not certify security.

## Overview

**What my-team is.** my-team runs a persistent local daemon that coordinates AI agents able to edit code. That makes
it a security-sensitive component even though it never leaves the machine.

**Sensitive assets:**
- project memory, decisions, and messages (they can contain proprietary design and code excerpts)
- the human's approval authority
- the daemon secret
- repository contents and git credentials the repository may reference

**Trust boundaries:**
1. The human's browser against every other website open in it.
2. Agent processes against the daemon. Agents act for the human but can be prompt-injected.
3. Repository content and git configuration, which are untrusted input.
4. Other operating-system users on the same machine, who are hostile.

**What is actually guaranteed** depends on how each agent is configured:

| Agent configuration | Approval floor (human-only actions) | Agent session identity | Direct database writes |
|---|---|---|---|
| Agent reaches my-team only through the MCP shim (Claude Code with the offered deny rules; OpenCode or Hermes with permission rules) | Holds. Human actions need the passphrase or passkey | Holds against confused or buggy clients | Not possible |
| Codex in its default unelevated Windows sandbox | Holds | **Not protected.** Verified 2026-09-23: sandboxed commands can read the daemon secret (even with an owner-only ACL) and reach loopback | Not possible: sandboxed writes to the data directory are denied |
| Agent with an unrestricted shell as the same OS user | Holds for API actions: no human credential exists on disk, and the passphrase hash is scrypt | Not protected | **Detected, not prevented.** The daemon raises an integrity alert and pauses auto-apply |
| Malware running as the same OS user | Out of scope | Out of scope | Out of scope |

## Security Principles

- **Least privilege.**
  - Agents get the narrowest operation set that lets them work.
  - Every human-only operation rejects agent credentials in exactly one place, the operation table.
  - The daemon has no command-execution endpoint.
- **No credential in a URL, a log, or a repository.** Secrets travel only in request bodies or signatures.
- **Untrusted by default.** Repository content, git configuration, agent messages, and agent-written memories are
  data. They are never instructions.
- **Fail closed on authority, open on convenience.**
  - A missing daemon never blocks an agent's work.
  - A failed check never grants an approval.
- **Say what is not protected.** Limits are written down (the table above, Known gaps) rather than discovered later.
- **Verify before claiming.** A control counts only when a test in the security suite exercises it.

## Testing Methods

| Method | Status | Evidence |
|---|---|---|
| Static analysis (SAST) | Planned (S1) | `ruff` security rules in CI, plus the pattern checks in `my-team scan` run against this repository |
| Dependency scanning (SCA) | Planned (S1) | `pip-audit` / `osv-scanner` on exact pins, and `pnpm audit` for the UI, in CI |
| Secret scanning | Planned (S2) | The shared redaction ruleset runs on every write and in CI over the repository |
| Dynamic testing (DAST) | Planned (S6) | Security suite against a running daemon: Host rebinding, Origin, CSRF, CSP, port squatting |
| Authentication and authorization tests | Planned (S1) | 403 matrix over (actor × owner × operation); code reuse; cookie expiry |
| Fuzz and input tests | Planned (S2) | Hypothesis on operation inputs; ReDoS corpus for scanner regexes; oversize bodies |
| Manual review | In use | Adversarial design review of the plan by four independent critics (2026-09-23) |
| Threat modelling | In use | This document's trust boundaries and guarantee matrix |
| Penetration test | N/A | Local single-user tool; revisit if multi-user or network access is ever added |

## Mandatory Security Rules

### Authentication

- Human sessions require a passphrase of at least 10 characters (scrypt hash) or a WebAuthn passkey.
- `my-team open` passes a one-time code through an owner-only file and a POST body. Codes are never placed in URLs.
- Session cookies are HttpOnly and SameSite=Strict, last 12 hours, and are revocable server-side.

### Authorization

- The approval floor is human-only:
  - decisions and intent documentation
  - accepting proposed tickets
  - done, cancel, pause, and reassign
  - sprint start and completion
  - settings
  - named agents
  - fetch, restore, purge, and delete
- Agent transitions must satisfy ownership conditions (holder, assignee, and claim epoch) inside the same
  conditional update.

### Secrets Management

- The daemon secret is 32 random bytes in the user data directory. The directory has an owner-only ACL on Windows
  and mode 0700 on POSIX, and `doctor` refuses to run on anything broader.
- Agents sign requests with HMAC, so the secret never crosses the socket.
- `.env*` files are never read. Only key names are taken from `*.example`.

### Data Protection

- Every free-text column of every table passes secret redaction before insert.
- Purge overwrites the text, rebuilds search entries, runs with `secure_delete`, checkpoints, vacuums, and flags
  backups that contain the purged data.
- Remote URLs are stored with credentials stripped.

### Input Validation

- Every operation input is a Pydantic model with size limits.
- Markdown renders without raw HTML. External images render as text, and links go through a confirmation.
- Mermaid runs in strict mode with a sanitised SVG and no `foreignObject`.

### Database Access

- Only the daemon writes, through one writer thread per project database.
- Settings: `trusted_schema=OFF`, `foreign_keys=ON`, `synchronous=FULL`.
- A database with a newer schema is refused.
- Restore accepts only files from `backups/` that pass the integrity, version, and schema checks.

### API Security

- The daemon listens on loopback only, IPv4 and IPv6.
- Requests are checked against a `Host` allowlist, and every request, GETs included, must carry both an `Origin`
  header and `X-My-Team: 1`.
- There is no CORS.
- The daemon sends CSP `default-src 'none'`, `frame-ancestors 'none'`, and `Referrer-Policy: no-referrer`.
- Clients verify the daemon with an HMAC challenge before sending data.

### Logging

- Log lines use structured event slugs with identity fields.
- Request bodies, query strings, prompts, and tokens are never logged.
- uvicorn access logs are off.

### Dependency Management

- Runtime dependencies are pinned exactly and scanned in CI.
- Releases go out through PyPI Trusted Publishing with attestations.
- The plugin marketplace is pinned to a tag and commit.
- Hooks refuse to run on a major-version mismatch between the plugin and the CLI.

### Agent Permissions

- Agents cannot write human instructions or mark anything human-confirmed.
- Content from other agents is wrapped with a per-response nonce and labelled with its trust level.
- Quotas and rate limits are keyed by (project, agent type), not by session.
- Chat confirmations are accepted only when the quote matches a human prompt captured by the harness hook, and only
  for questions, decisions, intent documentation, and accepting proposed tickets.

### Local Server Security

- The daemon starts only after explicit autostart consent and is never installed as an OS service.
- Clients refuse a daemon that fails the HMAC health challenge, and a port held by another process makes startup
  fail loudly.
- Git runs through one hardened wrapper:
  - a clean environment
  - fsmonitor disabled
  - an empty hooks directory
  - no external diff or textconv
  - a protocol allowlist
  - a preflight that disables git features for repositories whose configuration can run commands

### Data Retention and Deletion

- Everything is kept by default. Session notes expire. Captured human prompts are kept for 24 hours.
- Backups are daily, keeping 7, plus the last 3 pre-migration backups, kept for 30 days.
- Purge and project deletion are human-only. Deletion requires typed confirmation and removes the project's
  database and backups.

### Repository and Filesystem Boundaries

- Writes happen only in the data directory, the config directory, `.my-team/project.toml` (created once, exclusively),
  and the mapped documentation paths.
- Document writes build the path from an enum, hold a directory handle that is not a reparse point, and replace
  atomically.
- The scanner follows no symlinks or junctions and skips files over 1 MiB.

## Known gaps

- Same-user processes with an unrestricted shell can impersonate agent sessions and write the database directly.
  The latter is detected, not prevented.
- Codex's unelevated Windows sandbox can read the daemon secret. Codex's elevated sandbox mode has not been tested.
- When job breakaway is refused, the daemon runs inside its host application's job object (for example, the Claude
  Desktop tool tree) and may stop when that application exits. The next hook or tool call restarts it.
- Git 2.41, installed on the development machine, predates current security releases. `doctor` flags it and fetch
  stays disabled until git is upgraded.

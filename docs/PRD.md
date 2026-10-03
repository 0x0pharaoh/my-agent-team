# my-team — Product Requirements

## Overview

| Field | Value |
|---|---|
| Version | 1.0 |
| Date created | 2026-09-23 |
| Last updated | 2026-09-23 |
| Author | 0x0pharaoh |
| Status | Approved |
| Target Launch | v0.1.0 at the end of slice S6 |

Source: the implementation plan approved on 2026-09-23 (decisions D1–D19). Approved by the owner on 2026-09-23.

## Product Overview

my-team is a reusable engineering system for one developer who supervises several AI coding sessions at once. It
has three parts:

1. **A skill.** One set of rules and workflows that Claude Code, Codex, OpenCode, and Hermes all follow. It covers
   minimal comments, strict DRY, a short requirement review on every prompt, and five authoritative project
   documents.
2. **Adapters.** Thin packaging that plugs the skill into each agent's own commands, hooks, and MCP configuration.
3. **A local platform.** A Python daemon with SQLite storage and a browser dashboard. Agent sessions register with
   it, share searchable project memory, message each other, and work from a sprint and ticket board that the
   human controls.

The value is continuity and control. An agent starting a session receives the project's decisions and recent work
in one call instead of re-reading history. Two agents never take the same ticket. The human sees what every
session is doing and approves anything consequential from one screen.

## Problem Statement

Developers who run several AI coding agents in parallel lose track of three things:

- **Context between sessions.** Each new session starts blank or re-reads large histories. Decisions made in one
  session are invisible to the next, so agents repeat questions and contradict earlier choices.
- **Coordination between agents.** Agents working in one repository at the same time duplicate work, edit the
  same files, and have no channel to report blockers or findings to each other.
- **Consistency of output.** Each agent brings its own habits: redundant comments, copy-pasted logic, and
  undocumented architecture. Project documentation drifts from the code.

The affected user is a software engineer (solo or leading a small effort) who drives agents from a terminal or
desktop app on their own machine and remains accountable for what ships.

## Goals

- Re-running initialization on a project creates no duplicate projects, documents, tickets, agents, or memories.
- An agent obtains a relevant context pack of at most 2,000 tokens in one call at session start.
- No ticket ever has two holders, and no session ever holds two in-progress tickets. The database enforces this.
- Every live status in the dashboard shows its source and age, and nothing unreported is ever displayed.
- All five project documents pass structural validation, and every unconfirmed statement is visibly marked.
- After a forced kill at any point, the database passes an integrity check and no acknowledged write is lost.
- Per-prompt hook overhead averages 150 tokens or fewer, with p95 latency of 300 ms or less on Windows.
- No agent credential can perform an action reserved for the human.

## Target Users / Customers

| Segment | Description |
|---|---|
| Primary | Individual software engineers who run two or more AI coding agents concurrently on one workstation (Windows, macOS, or Linux) |
| Secondary | The AI agents themselves, as consumers of memory, tickets, messages, and documents |
| Later | Small teams sharing one project's memory (out of scope for v1: one human, one machine) |

No industry, geography, or age segmentation is defined. my-team is a developer tool distributed as open source
(MIT).

## Core Features

- **Commands:** `init`, `audit`, and `active {global|directory|session|one-time}`, available in every supported
  agent using that agent's native syntax.
  - Acceptance: a parser test covers every form for every agent.
  - Acceptance: repeated init is idempotent.
  - Acceptance: audit never modifies files.
- **Development rules:** minimal comments, a DRY decision rubric, and a compact requirement review on every prompt.
  - Acceptance: LLM evaluations show no narrative comments in generated diffs.
  - Acceptance: agents reuse existing helpers.
  - Acceptance: clear prompts get a one-line review, and ambiguous prompts get questions.
- **Project documents:** PRD, ARCHITECTURE, RULES, DESIGN, and SECURITY are generated from codebase evidence and
  human answers, and every unconfirmed statement is marked.
  - Acceptance: required sections are validated.
  - Acceptance: agents can auto-apply only code-derived sections, and the daemon decides which sections count.
- **Persistent memory:** typed records (facts, work summaries, notes, human instructions) with full-text search,
  supersede and retract instead of in-place edits, and staleness detection when referenced files change.
  - Acceptance: retrieval ranks the expected records in the top 3 on fixtures.
- **Agent seats and sessions:** human-nameable agent identities and sessions whose liveness is derived from
  heartbeats. Lineage survives `/clear` and context compaction.
  - Acceptance: a status chip never shows "active" more than 90 seconds after an agent stops.
- **Messaging:** direct, ticket-thread, broadcast, and human channels. Messages are store-and-forward with
  explicit read acknowledgement and live display in the dashboard.
  - Acceptance: a message surfaces at the recipient's next turn, and is read exactly once.
- **Tickets and sprints:** a Jira-style workflow, one active implementation ticket per session, file claims,
  dependencies, human-only closing, and pause/cancel/reassign with an immediate stop notice to the agent.
  - Acceptance: 20 concurrent claimants on one ticket produce exactly one winner.
- **Human dashboard:** Dashboard, Inbox, Board, Backlog, Agents, Messages, Memory, Docs, and Settings, secured by
  a passphrase login with an optional passkey.
  - Acceptance: Playwright suites cover every view.
  - Acceptance: an assignment reaches "in progress" within 1 second of the claim.
- **Repository status:** local and remote git state shown separately. Remote state is labelled with its last
  verified fetch time or "not verified".
  - Acceptance: fetch runs only on an explicit click.
- **Audit:** twelve inspection areas with file:line evidence, severity, confirmed-versus-potential labelling, and
  deduplicated remediation tickets.
  - Acceptance: planted violations are found.
  - Acceptance: `git status` is unchanged after an audit.
- **Adapters:** Claude Code, Codex, OpenCode (V2), and Hermes, plus a two-way bridge to Hermes's kanban in which
  my-team stays authoritative.
  - Acceptance: all four agents coordinate on shared tickets in one end-to-end test.

## Non-goals

- The platform never spawns or drives agents. Assigned work is picked up by a running session.
- No live push into running agent sessions in v1.
- No multi-human sync, LAN access, or cloud service.
- No embeddings or hosted models. Retrieval is lexical and offline.
- No automatic sync with Jira or GitHub Issues, and no automatic fixing of audit findings.
- No literal ten-million-token context. "10M context" means durable memory with budgeted retrieval.

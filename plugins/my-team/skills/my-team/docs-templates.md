# The five project documents

Every document starts with this status table:

| Field | Value |
|---|---|
| Version | 0.1 |
| Last updated | YYYY-MM-DD |
| Status | Draft |

Status values: Draft, In review, Approved, Not applicable (the human sets it), Superseded.

Conventions:
- Mark content taken from the code but not yet confirmed by the human with a line
  `> Inferred from <paths> — unconfirmed.` at the start of the section.
- Write unknowns as `TBD (Q-n)` and ask question Q-n in chat.
- Never invent requirements, users, design decisions, or security guarantees.

## docs/PRD.md

The PRD's Overview table replaces the status table: Version, Date created, Last updated, Author (the human owner, never
an agent), Status, Target Launch (a date, a milestone, or `TBD (Q-n)`).

Required sections:
- `## Overview`
- `## Product Overview`: what the product is and does, and its value.
- `## Problem Statement`: the real problem, who has it, and in what context.
- `## Goals`: measurable, as a bullet list.
- `## Target Users / Customers`: segments; industry, geography and age only when known.
- `## Core Features`: bullets, each with an acceptance line.

Optional: Non-goals, Success metrics, Assumptions, Open questions.

## docs/ARCHITECTURE.md

Required sections:
- `## Overview`: purpose, boundaries, major components, approach.
- `## High-Level Architecture`: a Mermaid flowchart of the real system, from users through the frontend, backend,
  services and data stores to external integrations. Every top-level source directory and deployable is either a
  node or listed under "Not shown".
- `## Technology Stack`: a table with columns Layer, Technology, Purpose, taken from the manifests.
- `## Folder Structure`: the important directories and their boundaries. Not a raw tree dump.

Optional: Data flow, Deployment, Integrations, Decision index.

## docs/RULES.md

Required sections:
- `## Overview`: purpose and authority of the rules.
- `## General Rules for LLMs and Humans`: doc references, plan-then-implement, minimal comments, strict DRY, review,
  testing, doc updates, human approval, agent communication.
- `## Technology and Coding Standards`: languages, frameworks, lint, format, styling, naming, error handling, tests,
  API conventions, dependencies. Taken from the actual config files.
- `## Project Structure`: monorepo, polyrepo or separate repositories, and their boundaries.
- `## Agent Skills`: a table with columns Name, Purpose, Applicable tasks, Activation requirements, Required
  integrations, Relevant docs, Conflicts. Follow it with the line: *A listed skill may not be installed; agents verify
  availability before relying on it.*

## docs/DESIGN.md

For a project with no UI, write only the status table with `Status: Not applicable`, after the human confirms.

Required sections:
- `## Overview`: visual identity, audience, objectives.
- `## Design Principles`: needs the human's approval.
- `## Color Palette`: a table with columns Token, Light, Dark, Role, covering primary, secondary, background, surface,
  text, border, success, warning, error, and interactive states. Needs the human's approval.
- `## Typography`: families with fallbacks, weights, type scale, headings, body, small text, line heights,
  responsive rules.
- `## UI Components`: library, ownership, reuse rules, tokens. It must contain `### Interaction States` (default,
  hover, focus-visible, active, disabled, loading, error, empty, selected) and `### Accessibility` (contrast, focus,
  keyboard, ARIA, reduced motion, target size).
- `## Spacing and Borders`: spacing scale, layout, border widths and colours, radius scale, shadows, responsive
  rules.

## docs/SECURITY.md

Put this line directly under the status table: *This document describes intended controls; it does not certify
security.* Never write "is secure", "fully secure", "guaranteed" or "no vulnerabilities".

Required sections:
- `## Overview`: security boundaries, sensitive assets, threat context.
- `## Security Principles`
- `## Testing Methods`: a table with columns Method, Status (in use / planned / N/A), Evidence. Cover SAST, dependency
  scanning, secret scanning, DAST, authn/authz tests, fuzz/input tests, manual review, threat modelling and
  penetration testing.
- `## Mandatory Security Rules`: one `###` subsection each for Authentication, Authorization, Secrets Management, Data
  Protection, Input Validation, Database Access, API Security, Logging, Dependency Management, Agent Permissions, Local
  Server Security, Data Retention and Deletion, plus any project-specific rules.

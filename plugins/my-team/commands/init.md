---
description: Initialize this repository as a my-team project
argument-hint: "[name] [KEY]"
disable-model-invocation: true
---
Initialize my-team for this repository, following the my-team skill.

1. Tell the human, in one short paragraph, exactly what will happen: `.my-team/project.toml` is created in the repository root (project id, name, ticket key), the repository is registered with the local my-team daemon, and my-team is switched on for this directory.
2. Ask for confirmation and wait for it.
3. On yes, call the `team_init` tool. Arguments from `$ARGUMENTS`: the first word is the project name and an all-caps second word is the ticket key; omit either when absent.
4. Continue with the rest of the my-team skill's init procedure: draft the five docs in `docs/` from docs-templates.md, asking questions in batches and writing each document only after the human approves it, then propose initial tickets.

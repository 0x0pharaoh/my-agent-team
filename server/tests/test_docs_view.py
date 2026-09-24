import pytest

pytestmark = pytest.mark.anyio

PRD = """# Demo — Product Requirements

## Overview

| Field | Value |
|---|---|
| Version | 0.2 |
| Status | Draft |

## Goals

> Inferred from package.json — unconfirmed.

Ship it. Target: TBD (Q-1), budget TBD (Q-2).

## Problem Statement

Confirmed text.
"""


async def test_docs_status_parses_fields_and_markers(repo, human):
    (repo / "docs").mkdir()
    (repo / "docs" / "PRD.md").write_text(PRD, encoding="utf-8")
    docs = {d["name"]: d for d in (await human.op("docs_status", {}))["data"]["docs"]}
    prd = docs["PRD"]
    assert prd["fields"] == {"Version": "0.2", "Status": "Draft"}
    assert [(s["title"], s["tbd"], s["inferred"]) for s in prd["sections"]] == [
        ("Overview", 0, False), ("Goals", 2, True), ("Problem Statement", 0, False)]
    assert (prd["tbd"], prd["inferred"]) == (2, 1)
    assert docs["DESIGN"] == {"name": "DESIGN", "present": False}


async def test_docs_status_is_human_only(agent):
    assert (await agent.op("docs_status", {}))["error"]["code"] == "human_only"

import hashlib
import json
import os
import re
import time
from pathlib import Path

from my_team.actor import Actor
from my_team.db.engine import Tx
from my_team.domain import events, messages
from my_team.errors import Conflict, Invalid, NotFound
from my_team.ids import new_id
from my_team.redact import redact

NAMES = ("PRD", "ARCHITECTURE", "RULES", "DESIGN", "SECURITY")
MAX_BYTES = 200_000
CODE_DERIVED = {"Technology Stack", "Folder Structure"}
_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$")
_INFERRED = re.compile(r"^> Inferred from .*unconfirmed", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LINK = re.compile(r"\[[^\]]+\]\(")
_HTML = re.compile(r"<[a-zA-Z][^>]*>")


def _read(root: Path, name: str) -> str | None:
    path = root / "docs" / f"{name}.md"
    try:
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            return None
        return path.read_bytes()[:MAX_BYTES].decode("utf-8", "replace")
    except OSError:
        return None


def summary(root: Path, name: str) -> dict:
    """Status-table fields plus per-section TBD and unconfirmed markers, parsed from the file on every read."""
    text = _read(root, name)
    if text is None:
        return {"name": name, "present": False}
    fields, in_table = {}, False
    for line in text.splitlines():
        if m := _ROW.match(line):
            in_table = True
            if m[1] not in ("Field", "---"):
                fields[m[1]] = m[2]
        elif in_table:
            break
    sections = []
    for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title, _, body = block.partition("\n")
        sections.append({"title": title.strip(), "tbd": len(re.findall(r"\bTBD \(Q-\d+\)", body)),
                         "inferred": bool(_INFERRED.search(body))})
    return {"name": name, "present": True, "fields": fields, "sections": sections, "text": text,
            "tbd": sum(s["tbd"] for s in sections), "inferred": sum(s["inferred"] for s in sections)}


def class_for(doc: str, anchor: str) -> str:
    return "code_derived" if doc == "ARCHITECTURE" and anchor.strip() in CODE_DERIVED else "intent"


def _section_of(text: str, anchor: str) -> str | None:
    want = anchor.strip()
    for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title, _, body = block.partition("\n")
        if title.strip() == want:
            return body
    return None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _shape_ok(content: str) -> bool:
    table = sum(1 for line in content.splitlines() if "|" in line) >= 2
    if not (table or "```" in content):
        return False
    return not (_HEADING.search(content) or _LINK.search(content) or _HTML.search(content))


def _path(root: Path, doc: str) -> Path:
    return root / "docs" / f"{doc}.md"


def _replace_section(text: str, anchor: str, content: str) -> str:
    lines, start = text.splitlines(keepends=True), None
    for i, line in enumerate(lines):
        if re.match(r"^##\s+", line) and line[4:].strip() == anchor.strip():
            start = i
            break
    body = content if content.endswith("\n") else content + "\n"
    if start is None:
        if not text:
            return f"## {anchor.strip()}\n{body}"
        sep = "" if text.endswith("\n") else "\n"
        return f"{text}{sep}\n## {anchor.strip()}\n{body}"
    end = next((j for j in range(start + 1, len(lines)) if re.match(r"^##\s+", lines[j])), len(lines))
    return "".join(lines[:start + 1]) + body + "".join(lines[end:])


def _write_file(path: Path, new_text: str) -> str:
    data = new_text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.my-team-tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return _digest(data)
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32) or attempt == 9:
                raise
            time.sleep(0.1)
    raise AssertionError("unreachable")


def _file_hash(root: Path, doc: str) -> str | None:
    try:
        return _digest(_path(root, doc).read_bytes())
    except OSError:
        return None


def _row(tx: Tx, id: str) -> dict:
    row = tx.one("SELECT * FROM doc_proposals WHERE id = ?", (id,))
    if row is None:
        raise NotFound("unknown_proposal", "No such proposal.")
    return _serialize(row)


def _serialize(row: dict) -> dict:
    return {"id": row["id"], "doc": row["doc"], "anchor": row["anchor"], "class": row["class"],
            "status": row["status"], "content": row["content"], "evidence": json.loads(row["evidence"]),
            "created_ms": row["created_ms"], "decided_ms": row["decided_ms"]}


def sweep(tx: Tx, root: Path) -> None:
    for tmp in (root / "docs").glob(".*.my-team-tmp"):
        tmp.unlink(missing_ok=True)
    for row in tx.all("SELECT * FROM doc_proposals WHERE status = 'applying'"):
        current = _file_hash(root, row["doc"])
        if current == row["target_hash"]:
            tx.execute("UPDATE doc_proposals SET status = 'applied' WHERE id = ?", (row["id"],))
        elif current == row["base_file_hash"]:
            _finish_apply(tx, root, row)
        else:
            tx.execute("UPDATE doc_proposals SET status = 'conflicted' WHERE id = ?", (row["id"],))


def _finish_apply(tx: Tx, root: Path, row: dict) -> dict:
    text = _path(root, row["doc"]).read_text(encoding="utf-8") if _file_hash(root, row["doc"]) else ""
    new_text = _replace_section(text, row["anchor"], row["content"])
    target = _digest(new_text.encode("utf-8"))
    tx.execute("UPDATE doc_proposals SET status = 'applying', target_hash = ? WHERE id = ?", (target, row["id"]))
    _write_file(_path(root, row["doc"]), new_text)
    tx.execute("UPDATE doc_proposals SET status = 'applied' WHERE id = ?", (row["id"],))
    return _row(tx, row["id"])


def propose(tx: Tx, actor: Actor, root: Path, now: int, doc: str, anchor: str, content: str,
            evidence: list) -> dict:
    if doc not in NAMES:
        raise Invalid("bad_doc", f"Doc must be one of {', '.join(NAMES)}.")
    anchor, content = anchor.strip(), redact(content.strip())
    if not anchor or not content:
        raise Invalid("empty_proposal", "Anchor and content are required.")
    sweep(tx, root)
    text = _path(root, doc).read_text(encoding="utf-8") if _file_hash(root, doc) else ""
    section = _section_of(text, anchor)
    kind = class_for(doc, anchor)
    if kind == "code_derived" and not _shape_ok(content):
        kind = "intent"
    row_id = new_id()
    tx.execute(
        "INSERT INTO doc_proposals (id, doc, anchor, class, base_section_hash, base_file_hash, content, evidence,"
        " status, author_session_id, created_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (row_id, doc, anchor, kind,
         _digest(section.encode("utf-8")) if section is not None else None,
         _file_hash(root, doc), content, json.dumps(evidence or []), actor.session_id, now))
    events.emit(tx, "doc.proposed", "proposal", row_id, actor, {"doc": doc, "anchor": anchor}, now)
    row = _row(tx, row_id)
    if kind == "code_derived":
        row = _finish_apply(tx, root, tx.one("SELECT * FROM doc_proposals WHERE id = ?", (row_id,)))
        tx.execute("UPDATE doc_proposals SET decided_ms = ? WHERE id = ?", (now, row_id))
        messages.send(tx, actor, now, to="human",
                      body=f"Auto-applied {doc} › {anchor} (unreviewed; revert from the Inbox).")
        return _row(tx, row_id)
    return row


def decide(tx: Tx, actor: Actor, root: Path, now: int, id: str, approve: bool) -> dict:
    sweep(tx, root)
    row = tx.one("SELECT * FROM doc_proposals WHERE id = ?", (id,))
    if row is None:
        raise NotFound("unknown_proposal", "No such proposal.")
    if row["status"] != "pending":
        raise Conflict("not_pending", f"Proposal is {row['status']}.")
    if not approve:
        tx.execute("UPDATE doc_proposals SET status = 'rejected', decided_ms = ? WHERE id = ?", (now, id))
        events.emit(tx, "doc.rejected", "proposal", id, actor, {"doc": row["doc"]}, now)
        return _row(tx, id)
    text = _path(root, row["doc"]).read_text(encoding="utf-8") if _file_hash(root, row["doc"]) else ""
    current = _section_of(text, row["anchor"])
    current_hash = _digest(current.encode("utf-8")) if current is not None else None
    if current_hash != row["base_section_hash"]:
        tx.execute("UPDATE doc_proposals SET status = 'conflicted', decided_ms = ? WHERE id = ?", (now, id))
        return _row(tx, id)
    row = _finish_apply(tx, root, row)
    tx.execute("UPDATE doc_proposals SET decided_ms = ? WHERE id = ?", (now, id))
    events.emit(tx, "doc.applied", "proposal", id, actor, {"doc": row["doc"], "anchor": row["anchor"]}, now)
    return _row(tx, id)


def listing(tx: Tx, status: str | None = None) -> list[dict]:
    rows = tx.all("SELECT * FROM doc_proposals WHERE status = ? ORDER BY created_ms", (status,)) \
        if status else tx.all("SELECT * FROM doc_proposals ORDER BY created_ms")
    return [_serialize(row) for row in rows]

import re
from pathlib import Path

NAMES = ("PRD", "ARCHITECTURE", "RULES", "DESIGN", "SECURITY")
MAX_BYTES = 200_000
_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$")
_INFERRED = re.compile(r"^> Inferred from .*unconfirmed", re.MULTILINE)


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

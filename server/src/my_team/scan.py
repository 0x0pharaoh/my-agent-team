from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from my_team.git import GitError
from my_team.git import run as git_run

MAX_BYTES = 1024 * 1024
MAX_LINE = 20_000
READ_BYTES = 8192
SKIP_DIRS = {"node_modules", "dist", "build", "vendor", ".venv"}
MANIFESTS = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "composer.json",
    "Gemfile",
    "pom.xml",
)
LINT_CONFIGS = {
    ".ruff.toml",
    "ruff.toml",
    ".flake8",
    ".prettierrc",
    ".prettierrc.json",
    ".eslintrc",
    ".eslintrc.json",
    "eslint.config.js",
    "biome.json",
    "mypy.ini",
    "pyrightconfig.json",
}
DOCS = {"PRD", "ARCHITECTURE", "RULES", "DESIGN", "SECURITY"}
SECRET_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{20,80}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,200}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,200}\b"),
    re.compile(r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----[\s\S]{0,8000}?-----END [A-Z ]{0,20}PRIVATE KEY-----"),
    re.compile(r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,512}\.[A-Za-z0-9_-]{10,512}\.[A-Za-z0-9_-]{10,512}\b"
    ),
    re.compile(
        r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9]{1,40}_){0,6}(?:api_?key|key|secret|token|password|passwd|pwd)"
        r"(?:_[A-Za-z0-9]{1,40}){0,6}(?![A-Za-z0-9])[ \t]*(?::=|=>|=|:)[ \t]*[\"']?([A-Za-z0-9_./+=-]{12,200})"
    ),
)
TODO_RE = re.compile(r"\b(?:TODO|FIXME|XXX)\b")
COLOR_RE = re.compile(
    r"#[0-9A-Fa-f]{3,8}\b|\brgba?\([^)\n]{1,120}\)|\bhsla?\([^)\n]{1,120}\)"
)
IMPORT_RE = re.compile(r"^\s*(?:import|from|using|package)\b")
COMMENT_RE = re.compile(r"^\s*(?:#|//|--|;|/\*|\*|\*/)")
DIRECTIVE_RE = re.compile(r"noqa|type:\s*ignore|eslint-disable|pragma", re.IGNORECASE)


@dataclass
class FileEntry:
    path: str
    mode: str | None = None
    blob: str | None = None


def scan(path: str | os.PathLike[str] = ".", changed: str | None = None) -> dict:
    requested = Path(path).resolve()
    root, git = _root(requested)
    files = _git_files(root, changed) if git else _walk_files(root)
    ticket_re = _ticket_re(root)
    inventory = _empty_inventory()
    skipped: Counter[str] = Counter()
    findings = []
    scanned = 0
    dry_windows: dict[tuple[str, ...], list[tuple[str, int]]] = defaultdict(list)

    for entry in files:
        rel, mode = entry.path, entry.mode
        rel = rel.replace("\\", "/")
        full = root / rel
        _inventory(inventory, rel, full)
        skip = _skip_before_open(root, rel, full, mode)
        if skip:
            skipped[skip] += 1
            continue
        meta = full.lstat()
        if meta.st_size > MAX_BYTES:
            skipped["too_large"] += 1
            continue
        with full.open("rb") as file:
            head = file.read(READ_BYTES)
            if b"\0" in head:
                skipped["binary"] += 1
                continue
            file.seek(0)
            data = file.read(MAX_BYTES + 1)
            if entry.blob is None and mode is None:
                entry.blob = _blob_id(data)
            text = data.decode("utf-8", "replace")
        lines = [line[:MAX_LINE] for line in text.splitlines()]
        if _generated(lines):
            skipped["generated"] += 1
            continue
        scanned += 1
        findings.extend(_security(rel, lines))
        findings.extend(_todos(rel, lines, ticket_re))
        findings.extend(_comments(rel, lines))
        findings.extend(_tokens(rel, lines))
        _dry_windows(rel, lines, dry_windows)

    findings.extend(_dry_findings(dry_windows))
    findings.sort(
        key=lambda item: (item["path"], item["line"], item["category"], item["rule"])
    )
    return {
        "root": str(root),
        "files_scanned": scanned,
        "skipped": dict(sorted(skipped.items())),
        "inventory": inventory,
        "findings": findings,
    }


def _root(path: Path) -> tuple[Path, bool]:
    try:
        return Path(
            git_run(path, "rev-parse", "--show-toplevel").strip()
        ).resolve(), True
    except GitError:
        return path, False


def _git_files(root: Path, changed: str | None) -> list[FileEntry]:
    files = _untracked_files(root)
    if changed:
        out = git_run(root, "diff", "--name-only", "-z", changed)
        return [FileEntry(name) for name in out.split("\0") if name] + files
    out = git_run(root, "ls-files", "-s", "-z")
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, name = entry.split("\t", 1)
        mode, blob = meta.split(" ", 2)[:2]
        files.append(FileEntry(name, mode, blob))
    return files


def _untracked_files(root: Path) -> list[FileEntry]:
    out = git_run(root, "ls-files", "--others", "--exclude-standard", "-z")
    return [FileEntry(name) for name in out.split("\0") if name]


def _walk_files(root: Path) -> list[FileEntry]:
    files = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in names:
            full = Path(base) / name
            files.append(FileEntry(full.relative_to(root).as_posix()))
    return files


def _skip_before_open(root: Path, rel: str, full: Path, mode: str | None) -> str | None:
    name = full.name
    if mode in {"120000", "160000"}:
        return "git_special"
    if _is_env(name):
        return "env"
    if any(part in SKIP_DIRS for part in Path(rel).parts):
        return "ignored_path"
    if _is_lockfile(name) or ".min." in name:
        return "ignored_path"
    try:
        st_mode = full.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISREG(st_mode):
        return "non_regular"
    try:
        full.relative_to(root)
    except ValueError:
        return "outside_root"
    return None


def _is_env(name: str) -> bool:
    return name == ".env" or name.startswith(".env.")


def _is_lockfile(name: str) -> bool:
    return name.endswith(".lock") or name in {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "uv.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
    }


def _blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _generated(lines: list[str]) -> bool:
    return any("@generated" in line or "DO NOT EDIT" in line for line in lines[:5])


def _empty_inventory() -> dict:
    return {
        "languages": {},
        "manifests": [],
        "lockfiles": [],
        "lint_format_configs": [],
        "test_dirs": [],
        "ci_files": [],
        "docs": {name: False for name in sorted(DOCS)},
        "env_example_keys": {},
    }


def _inventory(inventory: dict, rel: str, full: Path) -> None:
    suffix = full.suffix.lower()
    if suffix:
        inventory["languages"][suffix] = inventory["languages"].get(suffix, 0) + 1
    name = full.name
    lower = rel.lower()
    if (
        name in MANIFESTS
        or fnmatch.fnmatch(name, "requirements*.txt")
        or fnmatch.fnmatch(name, "build.gradle*")
    ):
        inventory["manifests"].append(rel)
    if _is_lockfile(name):
        inventory["lockfiles"].append(rel)
    if name in LINT_CONFIGS or name.startswith(".prettierrc"):
        inventory["lint_format_configs"].append(rel)
    if any(
        part.lower() in {"test", "tests", "__tests__"} for part in Path(rel).parts[:-1]
    ):
        first = rel.split("/", 1)[0]
        if first not in inventory["test_dirs"]:
            inventory["test_dirs"].append(first)
    if lower.startswith((".github/workflows/", ".gitlab-ci")) or lower == "jenkinsfile":
        inventory["ci_files"].append(rel)
    parts = Path(rel).parts
    if (
        len(parts) == 2
        and parts[0].lower() == "docs"
        and parts[1].lower().endswith(".md")
    ):
        stem = Path(parts[1]).stem.upper()
        if stem in DOCS:
            inventory["docs"][stem] = True
    if name.endswith(".example") and not _is_env(name):
        inventory["env_example_keys"][rel] = _example_keys(full)


def _example_keys(full: Path) -> list[str]:
    try:
        with full.open("rb") as file:
            head = file.read(MAX_BYTES)
    except OSError:
        return []
    if b"\0" in head:
        return []
    keys = []
    for raw in head.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            keys.append(key)
    return keys


def _ticket_re(root: Path) -> re.Pattern[str]:
    key = None
    project = root / ".my-team" / "project.toml"
    if project.is_file():
        try:
            key = tomllib.loads(project.read_text(encoding="utf-8")).get("key")
        except (OSError, tomllib.TOMLDecodeError):
            key = None
    if key:
        return re.compile(rf"\({re.escape(str(key))}-\d+\)")
    return re.compile(r"\([A-Z][A-Z0-9]+-\d+\)")


def _finding(
    category: str,
    rule: str,
    severity: str,
    confidence: str,
    path: str,
    line: int,
    evidence: str,
) -> dict:
    return {
        "category": category,
        "rule": rule,
        "severity": severity,
        "confidence": confidence,
        "path": path,
        "line": line,
        "evidence": evidence,
    }


def _mask(value: str) -> str:
    return value[:4] + "\u2026"


def _security(path: str, lines: list[str]) -> list[dict]:
    findings = []
    for number, line in enumerate(lines, 1):
        for pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
                findings.append(
                    _finding(
                        "security",
                        "security.secret",
                        "critical",
                        "confirmed",
                        path,
                        number,
                        _mask(value),
                    )
                )
                break
    return findings


def _todos(path: str, lines: list[str], ticket_re: re.Pattern[str]) -> list[dict]:
    findings = []
    for number, line in enumerate(lines, 1):
        if TODO_RE.search(line) and not ticket_re.search(line):
            findings.append(
                _finding(
                    "debt",
                    "debt.todo_without_ticket",
                    "low",
                    "potential",
                    path,
                    number,
                    line.strip(),
                )
            )
    return findings


def _is_comment(line: str) -> bool:
    stripped = line.strip()
    return (
        bool(stripped)
        and COMMENT_RE.match(stripped)
        and not DIRECTIVE_RE.search(stripped)
    )


def _comments(path: str, lines: list[str]) -> list[dict]:
    findings = []
    run_start = None
    for index, line in enumerate(lines + [""], 1):
        if _is_comment(line) and not (index == 1 and line.startswith("#!")):
            run_start = index if run_start is None else run_start
            continue
        if run_start is not None and index - run_start > 2:
            evidence = " ".join(
                item.strip() for item in lines[run_start - 1 : index - 1]
            )
            if not (
                run_start <= 5
                and re.search(r"copyright|license", evidence, re.IGNORECASE)
            ):
                findings.append(
                    _finding(
                        "code",
                        "code.comments",
                        "low",
                        "potential",
                        path,
                        run_start,
                        evidence[:200],
                    )
                )
        run_start = None
    return findings


def _tokens(path: str, lines: list[str]) -> list[dict]:
    if Path(path).suffix.lower() not in {
        ".tsx",
        ".jsx",
        ".ts",
        ".css",
        ".vue",
        ".svelte",
    }:
        return []
    if "token" in path.lower() or "theme" in path.lower():
        return []
    findings = []
    for number, line in enumerate(lines, 1):
        match = COLOR_RE.search(line)
        if match:
            findings.append(
                _finding(
                    "design",
                    "design.tokens",
                    "low",
                    "potential",
                    path,
                    number,
                    match.group(0),
                )
            )
    return findings


def _dry_windows(
    path: str, lines: list[str], windows: dict[tuple[str, ...], list[tuple[str, int]]]
) -> None:
    normalized = []
    for number, line in enumerate(lines, 1):
        text = " ".join(line.split())
        if not text or COMMENT_RE.match(text) or IMPORT_RE.match(text):
            continue
        normalized.append((number, text))
    for index in range(len(normalized) - 5):
        chunk = normalized[index : index + 6]
        key = tuple(text for _, text in chunk)
        if all(set(text) <= set("{}[]();,") for text in key):
            continue
        windows[key].append((path, chunk[0][0]))


def _dry_findings(windows: dict[tuple[str, ...], list[tuple[str, int]]]) -> list[dict]:
    by_path: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for locations in windows.values():
        if len(locations) < 2:
            continue
        evidence = " / ".join(f"{path}:{line}" for path, line in locations[:4])
        for path, line in locations:
            by_path[path].append((line, line + 5, evidence))

    findings = []
    for path, ranges in by_path.items():
        ranges.sort()
        current_start = current_end = None
        current_evidence = ""
        for start, end, evidence in ranges:
            if current_start is None or start > current_end:
                if current_start is not None:
                    findings.append(
                        _dry_finding(path, current_start, current_end, current_evidence)
                    )
                current_start, current_end, current_evidence = start, end, evidence
            else:
                current_end = max(current_end, end)
                current_evidence = evidence
        if current_start is not None:
            findings.append(
                _dry_finding(path, current_start, current_end, current_evidence)
            )
    return findings


def _dry_finding(path: str, start: int, end: int, evidence: str) -> dict:
    return _finding(
        "code",
        "code.dry",
        "medium",
        "potential",
        path,
        start,
        f"lines {start}-{end}; {evidence}",
    )


def dumps(result: dict) -> str:
    return json.dumps(result, sort_keys=True)

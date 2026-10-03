"""Graphify code graphs per project: a separate uv tool, built code-only (no API key) into graphify-out/."""
import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from my_team import git

log = logging.getLogger("my_team.graphify")
OUT = "graphify-out"
BUILDING: set[str] = set()


def cli() -> str | None:
    return shutil.which("graphify")


def python() -> str | None:
    """The interpreter of the graphifyy uv tool, which serves the graph over MCP (python -m graphify.serve)."""
    try:
        tools = subprocess.run(["uv", "tool", "dir"], capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    venv = Path(tools.stdout.strip()) / "graphifyy"
    exe = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(exe) if exe.is_file() else None


def graph_path(root: Path) -> Path:
    return root / OUT / "graph.json"


def status(root: Path) -> dict:
    graph = graph_path(root)
    info = {"installed": bool(cli()), "built_ms": None, "nodes": None, "edges": None, "building": str(root) in BUILDING}
    if graph.is_file():
        info["built_ms"] = int(graph.stat().st_mtime * 1000)
        try:
            data = json.loads(graph.read_text(encoding="utf-8"))
            info["nodes"] = len(data.get("nodes", []))
            info["edges"] = len(data.get("links", data.get("edges", [])))
        except (OSError, ValueError, AttributeError):
            pass
    return info


def _exclude(root: Path) -> None:
    """Keep graph output out of git without touching tracked files: .git/info/exclude is local to this clone."""
    common = Path(git.run(root, "rev-parse", "--git-common-dir").strip())
    exclude = (common if common.is_absolute() else root / common) / "info" / "exclude"
    current = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if f"{OUT}/" not in current.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(current + ("" if current.endswith("\n") or not current else "\n") + f"{OUT}/\n",
                           encoding="utf-8")


def build(root: Path, update: bool = False) -> None:
    """Code-only extraction (tree-sitter, no LLM); update is incremental. Errors are logged, never raised."""
    graphify = cli()
    if not graphify:
        return
    try:
        _exclude(root)
        args = ["update", str(root)] if update else ["extract", str(root), "--code-only"]
        subprocess.run([graphify, *args], cwd=root, capture_output=True, timeout=900, check=True,
                       stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, git.GitError) as exc:
        log.warning("graphify %s failed for %s: %s", "update" if update else "build", root, exc)


def build_in_background(root: Path) -> bool:
    if str(root) in BUILDING or not cli():
        return False
    BUILDING.add(str(root))

    def work():
        try:
            build(root)
        finally:
            BUILDING.discard(str(root))
    threading.Thread(target=work, daemon=True, name="graphify-build").start()
    return True


def mcp_server(root: Path) -> dict | None:
    """MCP server entry for runs, only once a graph exists."""
    interpreter = python()
    if not graph_path(root).is_file() or not interpreter:
        return None
    return {"command": interpreter, "args": ["-m", "graphify.serve", str(graph_path(root))]}

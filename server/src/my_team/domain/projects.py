import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from my_team import git
from my_team.db.engine import Tx
from my_team.errors import Conflict, Forbidden, Invalid, NotFound
from my_team.ids import ULID_PATTERN, new_id

PROJECT_FILE = Path(".my-team") / "project.toml"
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


@dataclass(frozen=True)
class RepoIdentity:
    root: Path
    git_common_dir: Path | None
    repo_key: str


def _key_for(path: Path) -> str:
    stat = os.stat(path)
    return f"{stat.st_dev:x}:{stat.st_ino:x}"


def identify(cwd: Path) -> RepoIdentity:
    cwd = Path(os.path.realpath(cwd))
    try:
        top, common = git.run(cwd, "rev-parse", "--show-toplevel", "--git-common-dir").splitlines()[:2]
    except (git.GitError, ValueError):
        return RepoIdentity(cwd, None, _key_for(cwd))
    root = Path(os.path.realpath(top))
    common_dir = Path(os.path.realpath(common if os.path.isabs(common) else cwd / common))
    return RepoIdentity(root, common_dir, _key_for(common_dir))


def read_project_file(root: Path) -> dict | None:
    path = root / PROJECT_FILE
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not ULID_PATTERN.match(str(data.get("id", ""))) or not KEY_PATTERN.match(str(data.get("key", ""))):
        raise Invalid("bad_project_file", f"{path} is malformed; expected id (ULID), name and key (e.g. MT).")
    return data


def _write_project_file(root: Path, project_id: str, name: str, key: str) -> None:
    path = root / PROJECT_FILE
    path.parent.mkdir(exist_ok=True)
    body = f'id = "{project_id}"\nname = "{name}"\nkey = "{key}"\n'
    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(body)


def resolve(tx: Tx, identity: RepoIdentity) -> dict | None:
    bound = tx.one("SELECT p.* FROM project_roots r JOIN projects p ON p.id = r.project_id WHERE r.repo_key = ?",
                   (identity.repo_key,))
    declared = read_project_file(identity.root)
    if bound:
        if declared and declared["id"] != bound["id"]:
            raise Conflict("project_file_mismatch", "This repository's project.toml names a different project than "
                           "the one it is bound to.", bound=bound["id"], declared=declared["id"])
        return {"id": bound["id"], "name": bound["name"], "key": bound["key"], "root": str(identity.root)}
    if declared:
        known = tx.one("SELECT id FROM projects WHERE id = ?", (declared["id"],))
        if known:
            raise Forbidden("project_root_unbound", "This repository claims a project that is bound to another "
                            "location. The human must link it as a clone or fork it as a new project.",
                            project_id=declared["id"], root=str(identity.root))
    return None


def init(tx: Tx, identity: RepoIdentity, name: str, key: str, now: int) -> dict:
    existing = resolve(tx, identity)
    if existing:
        return existing | {"created": False}
    declared = read_project_file(identity.root)
    if declared:
        project_id, name, key = declared["id"], declared["name"], declared["key"]
    else:
        if not KEY_PATTERN.match(key):
            raise Invalid("bad_key", "Ticket key must be 2-10 uppercase letters or digits, starting with a letter.")
        project_id = new_id()
    tx.execute("INSERT INTO projects (id, name, key, created_ms) VALUES (?, ?, ?, ?)", (project_id, name, key, now))
    tx.execute("INSERT INTO project_roots (repo_key, project_id, root_path, git_common_dir, bound_ms)"
               " VALUES (?, ?, ?, ?, ?)", (identity.repo_key, project_id, str(identity.root),
                                           str(identity.git_common_dir) if identity.git_common_dir else None, now))
    if not declared:
        _write_project_file(identity.root, project_id, name, key)
    return {"id": project_id, "name": name, "key": key, "root": str(identity.root), "created": True}


def get(tx: Tx, project_id: str) -> dict:
    row = tx.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if row is None:
        raise NotFound("unknown_project", "No such project.")
    roots = [r["root_path"] for r in tx.all("SELECT root_path FROM project_roots WHERE project_id = ?", (project_id,))]
    return {"id": row["id"], "name": row["name"], "key": row["key"], "roots": roots}


def all_projects(tx: Tx) -> list[dict]:
    return [get(tx, row["id"]) for row in tx.all("SELECT id FROM projects ORDER BY created_ms")]

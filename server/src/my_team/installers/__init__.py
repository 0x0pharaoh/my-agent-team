import shutil
import sys
from pathlib import Path


def cli_path() -> str:
    path = str(Path(shutil.which("my-team") or sys.argv[0]).resolve())
    if sys.platform == "win32" and not path.lower().endswith(".exe") and Path(path + ".exe").is_file():
        path += ".exe"
    return path


def checkout() -> Path | None:
    """The repository this package runs from (editable install), or None when installed from a wheel."""
    root = Path(__file__).resolve().parents[4]
    return root if (root / ".claude-plugin" / "marketplace.json").is_file() else None


def skill_source() -> Path:
    root = checkout()
    if root is None:
        raise FileNotFoundError("skill files ship with the repository checkout; install with `uv tool install --editable`")
    return root / "skills" / "my-team"


def confirm(plan: list[str], yes: bool) -> bool:
    print("This will:\n" + "".join(f"  {n}. {step}\n" for n, step in enumerate(plan, 1)))
    return yes or input("Proceed? [y/N] ").strip().lower() == "y"

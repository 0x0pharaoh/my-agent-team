import tomllib
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]


def test_packaging_inputs_exist():
    """Hatch force-includes and artifacts must resolve, or the wheel silently drops skills/UI."""
    project = tomllib.loads((SERVER / "pyproject.toml").read_text(encoding="utf-8"))
    build = project["tool"]["hatch"]["build"]
    missing = []
    for source, _target in build.get("force-include", {}).items():
        if not (SERVER / source).exists():
            missing.append(source)
    for pattern in build.get("targets", {}).get("wheel", {}).get("artifacts", []):
        if not list(SERVER.glob(pattern)):
            missing.append(pattern)
    assert not missing, f"packaging inputs missing: {missing}"

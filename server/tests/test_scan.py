import json
import subprocess
from pathlib import Path

from my_team.cli import main
from my_team.scan import scan


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_finds_planted_rules_and_skips(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    write(repo / ".my-team" / "project.toml", 'id = "p1"\nname = "Repo"\nkey = "MT"\n')
    write(repo / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(repo / "config.example", "API_KEY=leave_blank\nPASSWORD=\n")
    write(repo / "secrets.py", 'token = "abcdefghijklmnop"\n')
    write(repo / "todo.py", "# TODO: wire this later\n")
    write(repo / "comments.py", "# one\n# two\n# three\nx = 1\n")
    write(repo / "src" / "a.py", duplicate_body("a"))
    write(repo / "src" / "b.py", duplicate_body("b"))
    write(repo / "ui" / "button.tsx", 'export const color = "#aabbcc";\n')
    write(repo / ".env", "AWS_SECRET_ACCESS_KEY=abcdefghijklmnop\n")
    (repo / "large.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    (repo / "binary.dat").write_bytes(b"abc\0def")
    write(repo / "node_modules" / "ignored.js", "const token = 'abcdefghijklmnop';\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=test@example.com", "-c", "user.name=test",
                    "commit", "-qm", "fixture"], cwd=repo, check=True)

    result = scan(repo)
    rules = [finding["rule"] for finding in result["findings"]]

    assert result["root"] == str(repo)
    assert rules.count("security.secret") == 1
    assert rules.count("debt.todo_without_ticket") == 1
    assert rules.count("code.comments") == 1
    assert rules.count("design.tokens") == 1
    assert rules.count("code.dry") == 2
    assert result["skipped"]["env"] == 1
    assert result["skipped"]["too_large"] == 1
    assert result["skipped"]["binary"] == 1
    assert result["skipped"]["ignored_path"] == 1
    assert not any(finding["path"] == ".env" for finding in result["findings"])
    assert result["inventory"]["docs"]["ARCHITECTURE"] is True
    assert result["inventory"]["env_example_keys"]["config.example"] == [
        "API_KEY",
        "PASSWORD",
    ]

    assert main(["scan", str(repo)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["findings"] == result["findings"]

    write(repo / "todo.py", "# TODO: changed only\n")
    changed = scan(repo, "HEAD")
    assert [finding["path"] for finding in changed["findings"]] == ["todo.py"]


def test_scan_includes_untracked_files_before_first_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    write(repo / ".gitignore", "ignored.py\n")
    write(repo / "todo.py", "# TODO: first scan\n")
    write(repo / "ignored.py", "# TODO: ignored\n")

    result = scan(repo)

    assert [finding["path"] for finding in result["findings"]] == ["todo.py"]
    assert result["files_scanned"] == 2


def duplicate_body(name: str) -> str:
    return "\n".join(
        [
            f"value = {name!r}",
            "total = 0",
            "for item in items:",
            "    total += item",
            "average = total / len(items)",
            "print(average)",
            "return average",
        ]
    )

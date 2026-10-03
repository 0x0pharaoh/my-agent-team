import os
import subprocess

import pytest

from my_team.domain import repo as repo_domain

pytestmark = pytest.mark.anyio


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.fixture
def tracked(repo, tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    (repo / "a.txt").write_text("one")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "first")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "HEAD")
    (repo / "a.txt").write_text("two")
    git(repo, "commit", "-q", "-am", "second")
    (repo / "a.txt").write_text("three")
    (repo / "new.txt").write_text("x")
    return repo


async def test_status_reports_local_and_unverified_remote_state(tracked, human):
    state = (await human.op("repo_status", {}))["data"]
    assert state["commits"][0]["subject"] == "second" and len(state["commits"]) == 2
    assert state["changes"] == {"staged": 0, "modified": 1, "untracked": 2}  # new.txt and .my-team/
    assert (state["ahead"], state["behind"]) == (1, 0) and state["fetched_ms"] is None
    assert state["upstream"].startswith("origin/")


async def test_remote_credentials_are_stripped(tracked, human):
    git(tracked, "remote", "add", "creds", "https://user:s3cret@example.com/x.git")
    remotes = (await human.op("repo_status", {}))["data"]["remotes"]
    assert {"name": "creds", "url": "https://example.com/x.git"} in remotes
    assert "s3cret" not in str(remotes)


async def test_repo_controlled_command_config_disables_git(tracked, human):
    git(tracked, "config", "filter.evil.clean", "calc.exe")
    assert (await human.op("repo_status", {}))["data"]["blocked"] == ["filter.evil.clean"]
    assert (await human.op("repo_fetch", {}))["error"]["code"] == "git_config_untrusted"


async def test_fetch_only_allows_https_and_ssh(tracked, human, monkeypatch):
    monkeypatch.setattr(repo_domain, "WINDOWS_FLOOR", (0,))
    failed = (await human.op("repo_fetch", {}))["error"]
    assert failed["code"] == "fetch_failed" and "not allowed" in failed["message"]


@pytest.mark.skipif(os.name != "nt", reason="the version floor is Windows-only")
async def test_old_git_disables_fetch(tracked, human, monkeypatch):
    monkeypatch.setattr(repo_domain, "WINDOWS_FLOOR", (99,))
    assert (await human.op("repo_fetch", {}))["error"]["code"] == "git_too_old"


async def test_repo_ops_are_human_only(tracked, agent):
    assert (await agent.op("repo_status", {}))["error"]["code"] == "human_only"

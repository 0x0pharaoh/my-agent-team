import pytest

from my_team import doctor
from my_team.db import backup

pytestmark = pytest.mark.anyio


async def test_daily_backup_is_idempotent_forceable_and_pruned(agent, home):
    written = backup.backup_all()
    assert sorted(p.name.split("-")[0] for p in written)[-1] == "registry" and len(written) == 2
    assert backup.backup_all() == []
    assert len(backup.backup_all(force=True)) == 2
    folder = home / "data" / "backups"
    for day in range(1, 10):
        (folder / f"registry-2020-01-{day:02}.db").write_bytes(b"")
    backup.backup_all(force=True)
    assert len(list(folder.glob("registry-????-??-??.db"))) == backup.KEEP


async def test_doctor_passes_a_healthy_install_and_fails_a_corrupt_db(agent, home, capsys):
    doctor.run()
    report = capsys.readouterr().out
    assert "fail" not in report and "integrity ok" in report and "is private" in report
    (home / "data" / "projects" / "broken.db").write_bytes(b"not a database" * 100)
    assert doctor.run() == 1
    assert "fail  broken.db" in capsys.readouterr().out

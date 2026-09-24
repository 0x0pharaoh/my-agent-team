import subprocess

from my_team import config
from my_team.installers import checkout, cli_path, confirm


def install(yes: bool = False) -> int:
    root = checkout()
    steps = [["claude", "plugin", "marketplace", "add", str(root) if root else "0x0pharaoh/my-agent-team"],
             ["claude", "plugin", "install", "my-team@my-team", "--config", f"cli_path={cli_path()}"]]
    if not confirm(["let the my-team daemon start on demand (no OS service)", *(" ".join(s) for s in steps)], yes):
        return 1
    config.save(autostart=True)
    for step in steps:
        subprocess.run(step, check=False)
    print("\nDone. Set the dashboard passphrase with `my-team setup`, then run /my-team:init in a project.")
    return 0

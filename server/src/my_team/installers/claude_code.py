import subprocess

from my_team import __version__, config
from my_team.installers import checkout, cli_path, confirm


def install(yes: bool = False) -> int:
    root = checkout()
    marketplace = str(root) if root else f"https://github.com/0x0pharaoh/my-agent-team.git#v{__version__}"
    steps = [["claude", "plugin", "marketplace", "add", marketplace],
             ["claude", "plugin", "install", "my-team@my-team", "--config", f"cli_path={cli_path()}"]]
    if not confirm(["let the my-team daemon start on demand (no OS service)", *(" ".join(s) for s in steps)], yes):
        return 1
    config.save(autostart=True)
    for step in steps:
        proc = subprocess.run(step, check=False)
        if proc.returncode != 0:
            print(f"failed: {' '.join(step)} (rc={proc.returncode})")
            return 1
    print("\nDone. Set the dashboard passphrase with `my-team setup`, then run /my-team:init in a project.")
    return 0

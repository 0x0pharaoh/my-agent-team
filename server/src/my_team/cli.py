import argparse
import getpass
import importlib
import json
import os
import sys
import webbrowser

from my_team import __version__, activation


def _serve(args) -> int:
    from my_team.daemon.serve import run
    return run(args.port)


def _status(args) -> int:
    from my_team.client import DaemonUnavailable, connect
    try:
        client = connect(autostart=False)
    except DaemonUnavailable as exc:
        print(exc)
        return 1
    print(f"my-team daemon running on http://127.0.0.1:{client.port}")
    return 0


def _open(args) -> int:
    from my_team.client import DaemonUnavailable, connect
    try:
        client = connect()
    except DaemonUnavailable as exc:
        print(exc, file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{client.port}/"
    print(url)
    webbrowser.open(url)
    return 0


def _setup(args) -> int:
    from my_team.client import DaemonError, connect
    if not sys.stdin.isatty():
        print("my-team setup must be run interactively in a terminal.", file=sys.stderr)
        return 2
    first = getpass.getpass("Choose a dashboard passphrase (10+ characters): ")
    if getpass.getpass("Repeat it: ") != first:
        print("Passphrases do not match.", file=sys.stderr)
        return 2
    try:
        connect(autostart=True).registry("auth_setup", {"passphrase": first})
    except DaemonError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print("Passphrase set. Open the dashboard with `my-team open`.")
    return 0


def _call(args) -> int:
    from my_team.client import DaemonError, connect
    payload = json.loads(args.payload or "{}")
    client = connect()
    try:
        if args.project:
            data = client.project(args.project, args.op, payload, args.session)
        else:
            data = client.registry(args.op, payload)
    except DaemonError as exc:
        print(json.dumps({"error": exc.to_dict()}, indent=1))
        return 1
    print(json.dumps(data, indent=1))
    return 0


def _mcp(args) -> int:
    from my_team.shim.server import main
    return main()


def _scan(args) -> int:
    from my_team.scan import dumps, scan
    print(dumps(scan(args.path, args.changed)))
    return 0


def _hook(args) -> int:
    from my_team.hook import run
    return run(args.event, args.agent)


def _active(args) -> int:
    on = args.state != "off"
    scope = args.scope
    if scope in ("session", "one-time"):
        print("Session and one-time scopes are set from inside an agent session (/my-team:active).", file=sys.stderr)
        return 2
    activation.set_scope(scope, on, cwd=os.getcwd(), path=args.path)
    print(json.dumps(activation.resolve(activation.load(), "cli", None, args.path or os.getcwd())))
    return 0


def _install(args) -> int:
    try:
        module = importlib.import_module(f"my_team.installers.{args.agent.replace('-', '_')}")
    except ModuleNotFoundError:
        print(f"{args.agent}: adapter in progress.", file=sys.stderr)
        return 2
    return module.install(yes=args.yes)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="my-team", description="Local memory, messaging and tickets for AI agents.")
    parser.add_argument("--version", action="version", version=f"my-team {__version__}")
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser("serve", help="run the local daemon in the foreground")
    serve.add_argument("--port", type=int)
    serve.set_defaults(handler=_serve)
    commands.add_parser("status", help="is the daemon running?").set_defaults(handler=_status)
    commands.add_parser("open", help="open the dashboard").set_defaults(handler=_open)
    commands.add_parser("setup", help="set the dashboard passphrase (interactive)").set_defaults(handler=_setup)
    commands.add_parser("mcp", help="MCP stdio server for agents").set_defaults(handler=_mcp)

    scan = commands.add_parser("scan", help="scan a repository and print audit JSON")
    scan.add_argument("path", nargs="?", default=".")
    scan.add_argument("--changed")
    scan.set_defaults(handler=_scan)

    hook = commands.add_parser("hook", help="agent hook handler (reads the hook payload on stdin)")
    hook.add_argument("event", choices=["session-start", "prompt"])
    hook.add_argument("--agent", default="claude-code")
    hook.set_defaults(handler=_hook)

    call = commands.add_parser("call", help="invoke one operation (agent credentials)")
    call.add_argument("op")
    call.add_argument("payload", nargs="?")
    call.add_argument("--project")
    call.add_argument("--session")
    call.set_defaults(handler=_call)

    active = commands.add_parser("active", help="turn my-team on or off for a directory or globally")
    active.add_argument("scope", choices=["global", "directory"])
    active.add_argument("state", choices=["on", "off"], nargs="?", default="on")
    active.add_argument("--path")
    active.set_defaults(handler=_active)

    install = commands.add_parser("install", help="wire my-team into an agent")
    install.add_argument("agent", choices=["claude-code", "codex", "opencode", "hermes"])
    install.add_argument("--yes", action="store_true")
    install.set_defaults(handler=_install)

    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 0
    if os.name != "nt":
        os.umask(0o077)
    return args.handler(args)

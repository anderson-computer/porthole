"""Command-line parsing for porthole (no GTK imports, so it can be
unit-tested without a display)."""

from __future__ import annotations

import argparse
from typing import List

from . import __version__
from .models import Host, parse_target


USAGE_EPILOG = """\
examples:
  porthole                          open your saved sessions
  porthole sessions.json            open a session file
  porthole --connect admin@10.0.0.5
  porthole --connect admin@10.0.0.5:2222 --auth password
  porthole --host 10.0.0.5 --user admin --port 2222
  porthole --connect 10.0.0.5       asks for the username

--connect opens a tab and connects straight away. Anything missing (the
username; the password when no saved one exists) is asked for. Without
--auth/-i, a saved host with the same address, port and user is reused so
its saved password and options apply; otherwise the session is not added
to your tree."""


def build_parser(description: str = "Porthole") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="porthole", description=description,
        epilog=USAGE_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("session_file", nargs="?", help="session file (.json or MultiPuTTY .xml) to open")
    p.add_argument("-c", "--connect", metavar="[USER@]HOST[:PORT]", action="append", default=[],
                   help="open a tab and connect (repeatable); IPv6: [USER@][ADDR]:PORT")
    p.add_argument("--host", help="host to connect to (with --user/--port)")
    p.add_argument("-u", "--user", help="username for --host/--connect when not in the target")
    p.add_argument("-p", "--port", type=int, help="ssh port (default 22)")
    p.add_argument("--auth", choices=("default", "key", "agent", "password"), default="default",
                   help="authentication method for the new session (default: default)")
    p.add_argument("-i", "--identity", metavar="KEYFILE", help="private key (implies --auth key)")
    p.add_argument("--name", help="tab name for the new session")
    p.add_argument("-V", "--version", action="version", version=__version__)
    return p


def hosts_from_args(args) -> List[Host]:
    """Turn --connect/--host/--user/--port into Host objects. Raises
    ValueError for a bad target."""
    targets = list(args.connect)
    if args.host:
        targets.append(args.host)
    elif (args.user or args.port) and not targets:
        raise ValueError("--user/--port need --host or --connect")
    hosts = []
    for t in targets:
        user, host, port = parse_target(t)
        if not host or host.startswith("-"):
            raise ValueError(f"bad target: {t!r}")
        if args.port is not None and not 0 < args.port < 65536:
            raise ValueError(f"port out of range: {args.port}")
        user = user or (args.user or "").strip()
        if user.startswith("-") or any(c.isspace() for c in user):
            raise ValueError(f"bad username: {user!r}")
        h = Host(name=args.name or (f"{user}@{host}" if user else host), hostname=host,
                 port=port or args.port or 22, username=user, auth_method=args.auth)
        if args.identity:
            h.auth_method, h.private_key_path = "key", args.identity
        hosts.append(h)
    return hosts

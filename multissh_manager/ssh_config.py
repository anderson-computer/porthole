"""
Import Host entries from an OpenSSH client config (~/.ssh/config).

Each concrete alias (no wildcards, not negated) becomes a Host whose
hostname is the alias itself, so the real ssh client still applies every
option from the config file at connect time. HostName/User/Port/
IdentityFile/ProxyJump are copied into the entry only for display and
searching. Include directives are followed (relative to ~/.ssh).
"""

from __future__ import annotations

import glob
import os
import shlex
from pathlib import Path
from typing import Dict, List, Optional, Set

from .models import Folder, Host


def _tokens(line: str) -> List[str]:
    line = line.strip()
    if not line or line.startswith("#"):
        return []
    if "=" in line.split(None, 1)[0]:
        key, _, rest = line.partition("=")
        return [key.strip()] + (shlex.split(rest, comments=True) if rest.strip() else [])
    try:
        return shlex.split(line, comments=True)
    except ValueError:
        return line.split()


def _read_lines(path: Path, seen: Set[Path], base: Path) -> List[str]:
    path = path.expanduser()
    try:
        real = path.resolve()
    except OSError:
        return []
    if real in seen or not real.is_file():
        return []
    seen.add(real)
    out: List[str] = []
    try:
        text = real.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        toks = _tokens(line)
        if toks and toks[0].lower() == "include":
            for pattern in toks[1:]:
                pattern = os.path.expanduser(pattern)
                if not os.path.isabs(pattern):
                    pattern = str(base / pattern)
                for inc in sorted(glob.glob(pattern)):
                    out.extend(_read_lines(Path(inc), seen, base))
            continue
        out.append(line)
    return out


def parse_ssh_config(path: Optional[Path] = None) -> Folder:
    path = Path(path) if path else Path.home() / ".ssh" / "config"
    lines = _read_lines(path, set(), Path.home() / ".ssh")

    folder = Folder(name="SSH config")
    current: List[Host] = []
    opts: Dict[str, Dict[str, str]] = {}

    for line in lines:
        toks = _tokens(line)
        if not toks:
            continue
        key = toks[0].lower()
        args = toks[1:]
        if key == "host":
            current = []
            for alias in args:
                if any(c in alias for c in "*?!") or alias.lower() == "all":
                    continue
                h = Host(name=alias, hostname=alias, port=22)
                current.append(h)
                folder.hosts.append(h)
                opts[h.id] = {}
            continue
        if key == "match":
            current = []
            continue
        for h in current:
            # ssh uses the first value obtained for each option.
            opts[h.id].setdefault(key, " ".join(args))

    for h in folder.hosts:
        o = opts.get(h.id, {})
        real_host = o.get("hostname")
        if real_host and real_host != h.name:
            h.notes = f"HostName {real_host}"
        # User/ProxyJump stay in ssh config; shown in notes for searching.
        if o.get("user"):
            h.notes = (h.notes + "; " if h.notes else "") + f"User {o['user']}"
        if o.get("proxyjump"):
            h.notes = (h.notes + "; " if h.notes else "") + f"ProxyJump {o['proxyjump']}"

    return folder

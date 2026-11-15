"""Open a saved host in Skiff (the SFTP client) with its saved credentials.

Skiff's command line (1.3.0+):  skiff --host H [--user U] [--port P]
connects at once and asks for anything missing. Two more options let
Porthole hand over the rest of a host's credentials, if the installed
Skiff has them (detected from `skiff --help`):

  --key PATH         private key file (Porthole "Specific private key" hosts)
  --password-fd N    read the password from inherited file descriptor N

The password is never put in argv, the environment or a file. Porthole
creates a pipe, writes the password into it, closes its write end and
passes only the read end to Skiff (pass_fds), which reads it once at
startup and closes it. The secret only ever exists in the kernel pipe
buffer and in the two processes' memory. If the installed Skiff doesn't
support --password-fd, no password is sent and Skiff asks for it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Dict, FrozenSet, List, Optional, Tuple

from .models import Host

DEFAULT_SKIFF_PATH = "/usr/bin/skiff"
_caps_cache: Dict[Tuple[str, float], FrozenSet[str]] = {}


def find_skiff(configured: str = "") -> Optional[str]:
    """The configured Skiff path if it's an executable file, else `skiff`
    on PATH, else None."""
    path = os.path.expanduser((configured or "").strip() or DEFAULT_SKIFF_PATH)
    if os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    return shutil.which(os.path.basename(path) or "skiff") or shutil.which("skiff")


def skiff_capabilities(exe: str) -> FrozenSet[str]:
    """Long options listed by `skiff --help` (cached per binary + mtime)."""
    try:
        key = (exe, os.stat(exe).st_mtime)
    except OSError:
        return frozenset()
    if key not in _caps_cache:
        try:
            out = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=15,
                                 stdin=subprocess.DEVNULL,
                                 env=dict(os.environ, QT_QPA_PLATFORM="offscreen")).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _caps_cache[key] = frozenset(re.findall(r"(--[a-z][a-z0-9-]+)", out))
    return _caps_cache[key]


def build_argv(exe: str, host: Host, caps: FrozenSet[str], user: str = "",
               password_fd: Optional[int] = None) -> Tuple[List[str], List[str]]:
    """argv for Skiff, plus notes on credentials that couldn't be passed.
    Never contains a password."""
    notes: List[str] = []
    if "--host" not in caps:
        raise RuntimeError("This Skiff can't open a host from the command line "
                           "(needs Skiff 1.3.0 or later).")
    argv = [exe, "--host", host.hostname]
    user = (host.username or user or "").strip()
    if user:
        argv += ["--user", user]
    if host.port and int(host.port) != 22:
        argv += ["--port", str(int(host.port))]
    if host.auth_method == "key" and host.private_key_path:
        if "--key" in caps:
            argv += ["--key", os.path.expanduser(host.private_key_path)]
        else:
            notes.append("this Skiff can't take a key file (--key); it will try your "
                         "SSH agent / default keys, then ask for the password")
    if password_fd is not None:
        argv += ["--password-fd", str(password_fd)]
    if host.jump_host:
        notes.append("Skiff doesn't use the jump host")
    return argv, notes


def launch(exe: str, host: Host, user: str = "", password: Optional[str] = None,
           popen=subprocess.Popen) -> Tuple[List[str], List[str]]:
    """Start Skiff for host. Returns (argv, notes). The password, if any and
    if Skiff supports --password-fd, goes through an inherited pipe."""
    caps = skiff_capabilities(exe)
    notes: List[str] = []
    if password and "--password-fd" not in caps:
        notes.append("this Skiff can't receive a saved password (--password-fd); it will ask")
        password = None
    rfd = wfd = None
    try:
        if password:
            rfd, wfd = os.pipe()
            data = password.encode("utf-8")
            if len(data) >= 60000:  # pipe buffer is 64 KiB; never block the GUI
                raise ValueError("password too long to hand over")
            os.write(wfd, data)
            os.close(wfd)
            wfd = None
        argv, more = build_argv(exe, host, caps, user, rfd)
        notes += more
        popen(argv, pass_fds=(rfd,) if rfd is not None else (), close_fds=True,
              start_new_session=True, stdin=subprocess.DEVNULL,
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return argv, notes
    finally:
        for fd in (rfd, wfd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

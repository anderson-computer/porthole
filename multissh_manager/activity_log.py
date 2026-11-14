"""
Optional append-only activity log.

Records application activity (sessions opened/closed, tree edits, imports,
saves, broadcast commands, option changes) one line per event:

    2026-11-14 11:40:02  SESSION   connect  host="web1" target="root@10.0.0.1"

Never written: keystrokes typed into terminals, terminal output, or the
values of anything that looks like a secret. Text that may carry secrets
(broadcast commands, post-login commands, ssh arguments) goes through
redact() first, and a broadcast sent while a target tab is sitting at a
password/passphrase prompt is not recorded at all.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

REDACTED = "***"

_SECRET_WORDS = (r"password|passwd|passphrase|pwd|pass|secret|token|api[_-]?key|apikey|"
                 r"access[_-]?key|private[_-]?key|credentials?|auth")

_PATTERNS = [
    # scheme://user:password@host
    (re.compile(r"(://[^/\s:@]+:)[^@\s/]+(@)"), r"\1" + REDACTED + r"\2"),
    # sshpass -p SECRET
    (re.compile(r"(\bsshpass\s+-p\s*)\S+"), r"\1" + REDACTED),
    # mysql/mariadb -pSECRET (attached form only; bare -p prompts)
    (re.compile(r"(\b(?:mysql|mariadb|mysqldump|mysqladmin)\b[^|;&]*?\s-p)(?=\S)\S+"),
     r"\1" + REDACTED),
    # echo SECRET | sudo -S / passwd --stdin
    (re.compile(r"(\b(?:echo|printf)\s+)(\S+|'[^']*'|\"[^\"]*\")(\s*\|\s*(?:sudo\s+-S|passwd\b|chpasswd\b))"),
     r"\1" + REDACTED + r"\3"),
    # Authorization: Bearer xyz / -H "X-Api-Key: xyz"
    (re.compile(r"(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+"), r"\1" + REDACTED),
    # key=value / key: value / --key value / KEY=value env vars
    (re.compile(r"(?i)(\b[\w-]*(?:" + _SECRET_WORDS + r")[\w-]*\b\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
     r"\1" + REDACTED),
    (re.compile(r"(?i)(--?(?:" + _SECRET_WORDS + r")[\w-]*\s+)(\"[^\"]*\"|'[^']*'|[^\s-]\S*)"),
     r"\1" + REDACTED),
]


def redact(text: str) -> str:
    """Best-effort removal of secret values from a command line."""
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _quote(value) -> str:
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r", "\\r").replace("\n", "\\n")
    return f'"{s}"'


def default_log_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "multissh-manager" / "activity.log"


class ActivityLog:
    def __init__(self):
        self.enabled = False
        self.path: Optional[Path] = None
        self.last_error: Optional[str] = None

    def configure(self, enabled: bool, path: Optional[str]) -> Optional[str]:
        """Enable/disable logging. Returns an error message if the log file
        can't be written (logging is then left disabled)."""
        self.path = Path(path).expanduser() if path else default_log_path()
        self.enabled = False
        self.last_error = None
        if not enabled:
            return None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.close(fd)
        except OSError as e:
            self.last_error = f"Cannot write activity log {self.path}: {e.strerror or e}"
            return self.last_error
        self.enabled = True
        return None

    def log(self, category: str, event: str, **fields) -> None:
        if not self.enabled or self.path is None:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        parts = [f"{stamp}  {category.upper():<9} {event}"]
        parts += [f"{k}={_quote(v)}" for k, v in fields.items() if v not in (None, "")]
        line = " ".join(parts) + "\n"
        try:
            fd = os.open(str(self.path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode("utf-8", errors="replace"))
            finally:
                os.close(fd)
        except OSError as e:
            self.last_error = str(e)


# Shared instance used throughout the app.
activity = ActivityLog()

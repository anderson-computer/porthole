"""First-run migration from MultiSSH Manager 2.x (the name before Porthole).

If ~/.config/porthole doesn't exist yet but ~/.config/multissh-manager
does, its files (settings, sessions, vault, backups) are COPIED to
~/.config/porthole, and the activity log directory likewise from
~/.local/state/multissh-manager. Nothing in the old locations is changed
or removed, so MultiSSH Manager 2.x keeps working with its own data.
Settings that pointed inside the old folders are re-pointed at the copies;
paths elsewhere (e.g. a session file on a share) are left as they are.
Keychain entries are handled in secret_store.KeyringStore (read fallback).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from .models import atomic_write, config_dir

LEGACY_NAME = "multissh-manager"
MARKER = "MIGRATED-FROM-multissh-manager.txt"


def _config_base() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))


def _state_base() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state"))


def legacy_config_dir() -> Path:
    return _config_base() / LEGACY_NAME


def _copy_tree(src: Path, dst: Path) -> int:
    """Copy regular files only (no sockets/symlinks), 0700 dirs. Returns count."""
    tmp = dst.parent / f".{dst.name}.migrating-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    n = 0
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (tmp / rel).mkdir(mode=0o700, parents=True, exist_ok=True)
        for f in files:
            sp = Path(root) / f
            if sp.is_file() and not sp.is_symlink():
                shutil.copy2(sp, tmp / rel / f)
                n += 1
    os.chmod(tmp, 0o700)
    os.replace(tmp, dst)
    return n


def _repoint(value: str, old: Path, new: Path) -> str:
    if not value:
        return value
    p = Path(value).expanduser()
    try:
        rel = p.relative_to(old)
    except ValueError:
        return value
    return str(new / rel)


def migrate_legacy() -> Optional[str]:
    """Run once. Returns a short status message if anything was copied."""
    new_cfg, old_cfg = config_dir(), legacy_config_dir()
    if new_cfg.exists() or not old_cfg.is_dir():
        return None
    new_cfg.parent.mkdir(parents=True, exist_ok=True)
    try:
        copied = _copy_tree(old_cfg, new_cfg)
        old_state, new_state = _state_base() / LEGACY_NAME, _state_base() / "porthole"
        if old_state.is_dir() and not new_state.exists():
            new_state.parent.mkdir(parents=True, exist_ok=True)
            copied += _copy_tree(old_state, new_state)
        settings = new_cfg / "settings.json"
        if settings.is_file():
            data = json.loads(settings.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("data_file", "vault_file"):
                    if isinstance(data.get(key), str):
                        data[key] = _repoint(data[key], old_cfg, new_cfg)
                if isinstance(data.get("log_file"), str):
                    data["log_file"] = _repoint(data["log_file"], old_state, new_state)
                atomic_write(settings, json.dumps(data, indent=2), backup=False)
        (new_cfg / MARKER).write_text(
            f"Copied from {old_cfg} on {time.strftime('%Y-%m-%d %H:%M:%S %Z')} by Porthole.\n"
            f"The original files were left unchanged.\n", encoding="utf-8")
    except (OSError, ValueError) as e:
        return f"Could not copy MultiSSH Manager settings from {old_cfg}: {e}"
    return f"Imported {copied} file(s) from MultiSSH Manager ({old_cfg}); originals kept."

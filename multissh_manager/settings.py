"""User preferences, stored as JSON in ~/.config/multissh-manager/settings.json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import List

from .models import atomic_write, config_dir

SETTINGS_FILE = config_dir() / "settings.json"


@dataclass
class Settings:
    font: str = "Monospace 11"
    scrollback_lines: int = 10000
    theme: str = "dark"                  # dark | system
    confirm_close_connected: bool = True
    post_login_idle_ms: int = 700        # quiet time before post-login cmd
    restore_tabs: bool = False
    last_open_host_ids: List[str] = field(default_factory=list)
    window_width: int = 1200
    window_height: int = 760
    sidebar_width: int = 300

    @staticmethod
    def load() -> "Settings":
        s = Settings()
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        for k, v in data.items():
            if k in Settings.__dataclass_fields__ and v is not None:
                setattr(s, k, v)
        return s

    def save(self) -> None:
        try:
            atomic_write(SETTINGS_FILE, json.dumps(asdict(self), indent=2), backup=False)
        except OSError:
            pass

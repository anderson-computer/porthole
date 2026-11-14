"""
Loads the content shown on the About tab from a plain key=value config
file, so the author, tagline, and link can be changed without code edits.

Search order (later entries override earlier ones, field by field):
  1. Bundled default: multissh_manager/resources/about.conf
  2. System-wide:     /etc/multissh-manager/about.conf
  3. Per-user:        ~/.config/multissh-manager/about.conf
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from . import __version__
from .models import config_dir

RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
BUNDLED_CONFIG = RESOURCES_DIR / "about.conf"
SYSTEM_CONFIG = Path("/etc/multissh-manager/about.conf")
USER_CONFIG = config_dir() / "about.conf"
DEFAULT_LOGO = RESOURCES_DIR / "logo.svg"


LICENSE_NOTICE = (
    "MultiSSH Manager is free software, released under the MIT License. You may "
    "use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies "
    "of it, provided the copyright notice and permission notice are included in "
    "all copies or substantial portions of the software.\n\n"
    "MultiSSH Manager is provided \"as is\", without warranty of any kind, express "
    "or implied, including but not limited to the warranties of merchantability, "
    "fitness for a particular purpose and noninfringement. See the MIT License "
    "for more details."
)


@dataclass
class AboutInfo:
    # version always comes from the installed package, never from a file.
    app_name: str = "MultiSSH Manager"
    author: str = ""
    version: str = __version__
    tagline: str = "Tabbed SSH session manager for Linux"
    link_label: str = "Project page"
    link_url: str = ""
    logo_path: str = ""

    @property
    def logo_file(self) -> Path:
        if self.logo_path:
            p = Path(self.logo_path).expanduser()
            if p.is_file():
                return p
        return DEFAULT_LOGO


_FIELD_KEYS = {
    "app_name": ("app_name", "name"),
    "author": ("author",),
    "tagline": ("tagline", "subtitle"),
    "link_label": ("link_label", "link_text"),
    "link_url": ("link_url", "url", "more_info_url"),
    "logo_path": ("logo_path", "logo", "logo_file"),
}


def _parse_kv_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line[0] in "#;" or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().lower()] = value.strip()
    return values


def load_about_info() -> AboutInfo:
    info = AboutInfo()
    for config_path in (BUNDLED_CONFIG, SYSTEM_CONFIG, USER_CONFIG):
        values = _parse_kv_file(config_path)
        for field_name, keys in _FIELD_KEYS.items():
            for key in keys:
                if values.get(key):
                    setattr(info, field_name, values[key])
                    break
    return info

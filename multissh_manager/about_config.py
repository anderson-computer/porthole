"""Fixed content for the Help > About tab."""

from __future__ import annotations

from pathlib import Path

from . import __version__

LOGO_FILE = Path(__file__).resolve().parent / "resources" / "logo.svg"

APP_NAME = "MultiSSH Manager"
VERSION = __version__
TAGLINE = "Tabbed SSH session manager for Linux"
AUTHOR = "Mike Anderson"
LINKS = [
    ("Project page", "https://github.com/mike0615/multissh-manager-v2"),
    ("www.andersoncomputer.net", "https://www.andersoncomputer.net"),
]

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

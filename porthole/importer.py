"""
Importer for Multi PuTTY Manager (MPManager) session databases and the
closely related "PuTTY Connection Manager" / MTPuTTY XML exports.

MPManager's on-disk schema isn't published, so rather than hard-coding one
guessed layout this importer walks the XML tree generically:

  * any element carrying a recognizable host/address attribute becomes a
    Host;
  * any other element with children becomes a Folder.

Attribute names are matched case-insensitively against the synonym tables
below. If a particular export doesn't import cleanly, open it in a text
editor, see which attribute names it uses, and add them to the right tuple.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple

from .models import Folder, Host

NAME_ATTR = ("name", "title", "label", "sessionname", "displayname")
HOST_ATTR = ("hostname", "host", "address", "server", "ip", "ipaddress")
PORT_ATTR = ("port", "hostport")
USER_ATTR = ("username", "user", "login", "userid")
PROTO_ATTR = ("protocol", "proto", "sessiontype", "type")
KEY_ATTR = ("privatekeyfile", "keyfile", "identityfile", "sshkey", "pkey", "publickeyfile")
CMD_ATTR = ("postlogincommand", "autocommand", "command", "script", "runcommand")
EXTRA_ATTR = ("extraargs", "arguments", "args", "puttyargs")
NOTES_ATTR = ("description", "notes", "comment")

# Metadata tags that never represent folders.
IGNORED_TAGS = ("version", "settings", "options", "configuration", "preferences")


def _attr(elem: ET.Element, names: Tuple[str, ...]) -> Optional[str]:
    lower_map = {k.lower(): v for k, v in elem.attrib.items()}
    # Child elements like <HostName>x</HostName> are also accepted.
    for child in elem:
        if len(child) == 0 and child.text and child.text.strip():
            lower_map.setdefault(child.tag.lower(), child.text.strip())
    for n in names:
        if lower_map.get(n):
            return lower_map[n]
    return None


def _looks_like_host(elem: ET.Element) -> bool:
    return _attr(elem, HOST_ATTR) is not None


def _element_to_host(elem: ET.Element) -> Host:
    hostname = _attr(elem, HOST_ATTR) or ""
    port_raw = _attr(elem, PORT_ATTR)
    try:
        port = int(port_raw) if port_raw else 22
    except ValueError:
        port = 22
    protocol = (_attr(elem, PROTO_ATTR) or "ssh").lower()
    protocol = "telnet" if protocol == "telnet" else "ssh"
    key_path = _attr(elem, KEY_ATTR) or ""
    return Host(
        name=_attr(elem, NAME_ATTR) or hostname or "Imported host",
        hostname=hostname,
        port=port,
        username=_attr(elem, USER_ATTR) or "",
        protocol=protocol,
        auth_method="key" if key_path else "default",
        private_key_path=key_path,
        post_login_command=_attr(elem, CMD_ATTR) or "",
        extra_ssh_args=_attr(elem, EXTRA_ATTR) or "",
        notes=_attr(elem, NOTES_ATTR) or "",
    )


def _walk(elem: ET.Element, parent: Folder) -> None:
    if (elem.tag or "").lower() in IGNORED_TAGS:
        return

    if _looks_like_host(elem):
        parent.hosts.append(_element_to_host(elem))
        # A connection element may also group nested connections.
        nested = [c for c in elem if _looks_like_host(c) or len(c)]
        if nested:
            sub = Folder(name=_attr(elem, NAME_ATTR) or elem.tag)
            for child in nested:
                _walk(child, sub)
            if sub.hosts or sub.folders:
                parent.folders.append(sub)
        return

    if len(elem):
        name = _attr(elem, NAME_ATTR)
        folder = Folder(name=name or elem.tag)
        folder.unnamed = name is None  # structural wrapper, e.g. <Sessions>
        for child in elem:
            _walk(child, folder)
        if folder.hosts or folder.folders:
            parent.folders.append(folder)


def parse_file(path) -> Folder:
    """Parse a MultiPuTTY-family XML database and return it as a Folder tree.

    Raises ValueError if the file isn't XML or contains no hosts."""
    path = Path(path)
    raw = path.read_bytes()
    if not looks_like_xml(raw):
        raise ValueError(f"{path.name} does not look like an XML session database.")
    try:
        root_elem = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError(f"{path.name} is not valid XML: {e}") from e

    top = Folder(name=path.stem)
    if _looks_like_host(root_elem):
        top.hosts.append(_element_to_host(root_elem))
    else:
        for child in root_elem:
            _walk(child, top)
    # Unwrap lone structural wrappers (e.g. <Database><Sessions>...).
    while (not top.hosts and len(top.folders) == 1
           and getattr(top.folders[0], "unnamed", False)):
        only = top.folders[0]
        top.folders, top.hosts = only.folders, only.hosts

    if not any(True for _ in top.walk_hosts()):
        raise ValueError(
            f"No hosts found in {path.name}. If this is a MultiPuTTY export, "
            "add its attribute names to the tables at the top of importer.py."
        )
    return top


def looks_like_xml(head: bytes) -> bool:
    text = head[:512].decode("utf-8", errors="ignore").lstrip("﻿ \t\r\n")
    return text.startswith("<")


def looks_like_multiputty_file(path) -> bool:
    try:
        with open(path, "rb") as fh:
            return looks_like_xml(fh.read(512))
    except OSError:
        return False

"""
Data model for MultiSSH Manager.

A session store is a tree of Folder nodes containing Host entries (leaf
nodes). The native on-disk format is plain JSON so it's easy to inspect,
version-control, and hand-edit. MultiPuTTY-style XML databases and
~/.ssh/config files can be imported (see importer.py / ssh_config.py).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

PROTOCOLS = ("ssh", "telnet")
AUTH_METHODS = ("default", "key", "agent", "password")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Host:
    """A single saved connection."""

    id: str = field(default_factory=_new_id)
    name: str = "New Host"
    hostname: str = ""
    port: int = 22
    username: str = ""
    protocol: str = "ssh"            # ssh | telnet
    auth_method: str = "default"     # default | key | agent | password
    private_key_path: str = ""
    jump_host: str = ""              # ssh -J value, e.g. user@bastion:22
    post_login_command: str = ""     # one or more lines, sent after login
    extra_ssh_args: str = ""         # shell-quoted extra args passed to ssh
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Host":
        h = Host()
        for f in Host.__dataclass_fields__:
            v = d.get(f)
            if v is not None:
                setattr(h, f, v)
        try:
            h.port = int(h.port)
        except (TypeError, ValueError):
            h.port = 22
        # v1 stores used "password" as the default for every host even
        # though it never changed the ssh command; treat it as "default".
        if h.auth_method not in AUTH_METHODS:
            h.auth_method = "default"
        if h.protocol not in PROTOCOLS:
            h.protocol = "ssh"
        if not h.id:
            h.id = _new_id()
        return h

    def clone(self) -> "Host":
        d = self.to_dict()
        d["id"] = _new_id()
        d["name"] = f"{self.name} (copy)"
        return Host.from_dict(d)

    @property
    def target(self) -> str:
        return f"{self.username}@{self.hostname}" if self.username else self.hostname

    def matches(self, needle: str) -> bool:
        needle = needle.lower()
        return any(
            needle in (s or "").lower()
            for s in (self.name, self.hostname, self.username, self.notes)
        )

    def build_argv(self) -> List[str]:
        """Build the argv list that launches this host's session."""
        if self.protocol == "telnet":
            argv = ["telnet", self.hostname]
            if self.port and int(self.port) != 23:
                argv.append(str(self.port))
            return argv

        argv = ["ssh"]
        if self.port and int(self.port) != 22:
            argv += ["-p", str(self.port)]
        if self.auth_method == "key" and self.private_key_path:
            argv += ["-i", os.path.expanduser(self.private_key_path),
                     "-o", "IdentitiesOnly=yes"]
        elif self.auth_method == "password":
            argv += ["-o", "PreferredAuthentications=keyboard-interactive,password",
                     "-o", "PubkeyAuthentication=no"]
        if self.jump_host:
            argv += ["-J", self.jump_host]
        if self.extra_ssh_args:
            argv += shlex.split(self.extra_ssh_args)
        argv.append(self.target)
        return argv

    def command_preview(self) -> str:
        return " ".join(shlex.quote(a) for a in self.build_argv())


@dataclass
class Folder:
    """A folder node; may contain child folders and/or hosts."""

    id: str = field(default_factory=_new_id)
    name: str = "New Folder"
    folders: List["Folder"] = field(default_factory=list)
    hosts: List[Host] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "folders": [f.to_dict() for f in self.folders],
            "hosts": [h.to_dict() for h in self.hosts],
        }

    @staticmethod
    def from_dict(d: dict) -> "Folder":
        f = Folder(id=d.get("id") or _new_id(), name=d.get("name", "Folder"))
        f.folders = [Folder.from_dict(c) for c in d.get("folders", [])]
        f.hosts = [Host.from_dict(h) for h in d.get("hosts", [])]
        return f

    def walk_hosts(self) -> Iterator[Host]:
        yield from self.hosts
        for sub in self.folders:
            yield from sub.walk_hosts()

    def walk_folders(self) -> Iterator["Folder"]:
        yield self
        for sub in self.folders:
            yield from sub.walk_folders()

    def find_folder(self, folder_id: str) -> Optional["Folder"]:
        for f in self.walk_folders():
            if f.id == folder_id:
                return f
        return None

    def find_host(self, host_id: str) -> Optional[Host]:
        for h in self.walk_hosts():
            if h.id == host_id:
                return h
        return None

    def parent_of(self, node_id: str) -> Optional["Folder"]:
        """Return the folder that directly contains the host/folder id."""
        for f in self.walk_folders():
            if any(h.id == node_id for h in f.hosts):
                return f
            if any(c.id == node_id for c in f.folders):
                return f
        return None

    def contains_folder(self, folder_id: str) -> bool:
        return any(f.id == folder_id for f in self.walk_folders())

    def remove_host(self, host_id: str) -> Optional[Host]:
        parent = self.parent_of(host_id)
        if parent is None:
            return None
        for h in parent.hosts:
            if h.id == host_id:
                parent.hosts.remove(h)
                return h
        return None

    def remove_folder(self, folder_id: str) -> Optional["Folder"]:
        parent = self.parent_of(folder_id)
        if parent is None:
            return None
        for f in parent.folders:
            if f.id == folder_id:
                parent.folders.remove(f)
                return f
        return None

    def move(self, node_id: str, dest_folder_id: str, index: int = -1) -> bool:
        """Move a host or folder into another folder. Refuses to move a
        folder into itself or one of its own descendants."""
        dest = self.find_folder(dest_folder_id)
        if dest is None or node_id == self.id:
            return False
        folder = self.find_folder(node_id)
        if folder is not None:
            if folder.contains_folder(dest_folder_id):
                return False
            self.remove_folder(node_id)
            lst = dest.folders
            lst.insert(index if 0 <= index <= len(lst) else len(lst), folder)
            return True
        host = self.find_host(node_id)
        if host is not None:
            self.remove_host(node_id)
            lst = dest.hosts
            lst.insert(index if 0 <= index <= len(lst) else len(lst), host)
            return True
        return False

    def sort(self) -> None:
        self.folders.sort(key=lambda f: f.name.lower())
        self.hosts.sort(key=lambda h: h.name.lower())
        for sub in self.folders:
            sub.sort()

    def count(self) -> Tuple[int, int]:
        hosts = sum(1 for _ in self.walk_hosts())
        folders = sum(1 for _ in self.walk_folders()) - 1
        return folders, hosts


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "multissh-manager"


def atomic_write(path: Path, text: str, backup: bool = True) -> None:
    """Write text to path atomically, keeping the previous version as
    path.bak so a crash mid-save can never truncate the session store."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SessionStore:
    """Owns the root Folder tree and loads/saves the native JSON format."""

    FORMAT_NAME = "multissh-manager"
    FORMAT_VERSION = 2

    def __init__(self, root: Optional[Folder] = None, path: Optional[Path] = None):
        self.root = root or Folder(id="root", name="Sessions")
        self.path = path

    def to_json(self) -> str:
        payload = {
            "format": self.FORMAT_NAME,
            "version": self.FORMAT_VERSION,
            "root": self.root.to_dict(),
        }
        return json.dumps(payload, indent=2)

    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path or self.path)
        atomic_write(target, self.to_json())
        self.path = target
        return target

    @staticmethod
    def load(path: Path) -> "SessionStore":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "root" not in data:
            raise ValueError(f"{path.name} is not a MultiSSH Manager session file")
        root = Folder.from_dict(data["root"])
        return SessionStore(root=root, path=path)

    @staticmethod
    def default_path() -> Path:
        return config_dir() / "sessions.json"

    def merge(self, other_root: Folder, into: Optional[Folder] = None) -> None:
        """Merge another tree in as a new folder without touching existing
        entries."""
        (into or self.root).folders.append(other_root)

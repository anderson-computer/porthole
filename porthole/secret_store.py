"""
Password storage backends.

  none      passwords are never stored
  keyring   the desktop's Secret Service keychain (GNOME Keyring / KWallet)
            via libsecret. Unlocked with your login; works offline.
  vault     an encrypted file (AES-256-GCM, key derived from a master
            password with scrypt). Works anywhere, including headless and
            airgapped machines; unlocked once per app session.

Passwords are keyed by host id. Nothing here ever writes a password to the
session file, settings, logs, argv or the environment.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Dict, Optional

from .models import atomic_write, config_dir

BACKEND_NONE = "none"
BACKEND_KEYRING = "keyring"
BACKEND_VAULT = "vault"
BACKEND_LABELS = {
    BACKEND_NONE: "Don't save passwords",
    BACKEND_KEYRING: "System keychain (GNOME Keyring / Secret Service)",
    BACKEND_VAULT: "Encrypted vault file (master password)",
}

MIN_MASTER_LEN = 8


class VaultError(Exception):
    pass


def default_vault_path() -> Path:
    return config_dir() / "vault.json"


# ---------------------------------------------------------------------------
# Encrypted file vault
# ---------------------------------------------------------------------------
class FileVault:
    FORMAT = "porthole-vault"
    # Vaults written by MultiSSH Manager 2.x (before the rename) still open;
    # they're re-written in the Porthole format on the next change.
    LEGACY_FORMATS = ("multissh-manager-vault",)
    VERSION = 1
    # scrypt cost: ~0.1-0.3 s on a desktop CPU, 32 MiB of memory.
    SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1

    def __init__(self, path: Path):
        self.path = Path(path)
        self._key: Optional[bytes] = None
        self._salt: Optional[bytes] = None
        self._data: Dict[str, str] = {}

    # --- state ------------------------------------------------------------
    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def lock(self) -> None:
        self._key = None
        self._data = {}

    # --- crypto -----------------------------------------------------------
    @classmethod
    def _derive(cls, master: str, salt: bytes) -> bytes:
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        kdf = Scrypt(salt=salt, length=32, n=cls.SCRYPT_N, r=cls.SCRYPT_R, p=cls.SCRYPT_P)
        return kdf.derive(master.encode("utf-8"))

    def create(self, master: str) -> None:
        if self.exists:
            raise VaultError(f"A vault already exists at {self.path}")
        if len(master) < MIN_MASTER_LEN:
            raise VaultError(f"Master password must be at least {MIN_MASTER_LEN} characters")
        self._salt = os.urandom(16)
        self._key = self._derive(master, self._salt)
        self._data = {}
        self._write()

    def unlock(self, master: str) -> None:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
            fmt = blob.get("format")
            if fmt != self.FORMAT and fmt not in self.LEGACY_FORMATS:
                raise VaultError(f"{self.path} is not a Porthole vault")
            kdf = blob["kdf"]
            salt = base64.b64decode(kdf["salt"])
            nonce = base64.b64decode(blob["nonce"])
            ct = base64.b64decode(blob["ciphertext"])
        except (OSError, ValueError, KeyError) as e:
            raise VaultError(f"Cannot read vault {self.path}: {e}") from e
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        key = Scrypt(salt=salt, length=32, n=int(kdf["n"]), r=int(kdf["r"]),
                     p=int(kdf["p"])).derive(master.encode("utf-8"))
        try:
            plain = AESGCM(key).decrypt(nonce, ct, str(fmt).encode())
        except InvalidTag:
            raise VaultError("Wrong master password (or the vault file was modified).")
        self._data = json.loads(plain.decode("utf-8"))
        self._key, self._salt = key, salt

    def change_master(self, old: str, new: str) -> None:
        if len(new) < MIN_MASTER_LEN:
            raise VaultError(f"Master password must be at least {MIN_MASTER_LEN} characters")
        self.unlock(old)
        self._salt = os.urandom(16)
        self._key = self._derive(new, self._salt)
        self._write()

    def _write(self) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        assert self._key is not None and self._salt is not None
        nonce = os.urandom(12)
        ct = AESGCM(self._key).encrypt(
            nonce, json.dumps(self._data).encode("utf-8"), self.FORMAT.encode())
        blob = {
            "format": self.FORMAT, "version": self.VERSION, "cipher": "AES-256-GCM",
            "kdf": {"name": "scrypt", "n": self.SCRYPT_N, "r": self.SCRYPT_R,
                    "p": self.SCRYPT_P, "salt": base64.b64encode(self._salt).decode()},
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ct).decode(),
        }
        atomic_write(self.path, json.dumps(blob, indent=2), backup=True)

    # --- secrets ----------------------------------------------------------
    def _need_unlocked(self):
        if not self.unlocked:
            raise VaultError("The vault is locked.")

    def get(self, host_id: str) -> Optional[str]:
        self._need_unlocked()
        return self._data.get(host_id)

    def set(self, host_id: str, password: str) -> None:
        self._need_unlocked()
        self._data[host_id] = password
        self._write()

    def delete(self, host_id: str) -> None:
        self._need_unlocked()
        if self._data.pop(host_id, None) is not None:
            self._write()

    def ids(self):
        self._need_unlocked()
        return list(self._data)


# ---------------------------------------------------------------------------
# Secret Service keychain
# ---------------------------------------------------------------------------
class KeyringStore:
    SCHEMA_NAME = "us.andersoncomputer.Porthole.Password"
    # Entries saved by MultiSSH Manager 2.x. Read as a fallback and copied to
    # the Porthole schema on first use; the old entries are left in place.
    LEGACY_SCHEMA_NAME = "us.andersoncomputer.MultiSSHManager.Password"

    def __init__(self):
        self._schema = None
        self._legacy_schema = None
        self.error: Optional[str] = None
        try:
            import gi
            gi.require_version("Secret", "1")
            from gi.repository import Secret
            self._Secret = Secret
            self._schema = Secret.Schema.new(
                self.SCHEMA_NAME, Secret.SchemaFlags.NONE,
                {"host_id": Secret.SchemaAttributeType.STRING})
            self._legacy_schema = Secret.Schema.new(
                self.LEGACY_SCHEMA_NAME, Secret.SchemaFlags.NONE,
                {"host_id": Secret.SchemaAttributeType.STRING})
        except (ImportError, ValueError) as e:
            self.error = f"libsecret is not available: {e}"

    @property
    def available(self) -> bool:
        if self._schema is None:
            return False
        try:
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                "org.freedesktop.DBus", "NameHasOwner",
                                GLib.Variant("(s)", ("org.freedesktop.secrets",)),
                                None, Gio.DBusCallFlags.NONE, 2000, None)
            if res.unpack()[0]:
                return True
            # Activatable but not yet running also counts.
            res = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                "org.freedesktop.DBus", "ListActivatableNames",
                                None, None, Gio.DBusCallFlags.NONE, 2000, None)
            return "org.freedesktop.secrets" in res.unpack()[0]
        except Exception as e:  # no session bus, etc.
            self.error = str(e)
            return False

    def get(self, host_id: str) -> Optional[str]:
        try:
            pw = self._Secret.password_lookup_sync(self._schema, {"host_id": host_id}, None)
            if pw is None and self._legacy_schema is not None:
                pw = self._Secret.password_lookup_sync(
                    self._legacy_schema, {"host_id": host_id}, None)
                if pw is not None:
                    try:  # copy forward; keep the MultiSSH Manager entry
                        self.set(host_id, pw, f"Porthole ({host_id})")
                    except VaultError:
                        pass
            return pw
        except Exception as e:
            raise VaultError(f"Keychain lookup failed: {e}") from e

    def set(self, host_id: str, password: str, label: str = "") -> None:
        try:
            ok = self._Secret.password_store_sync(
                self._schema, {"host_id": host_id}, self._Secret.COLLECTION_DEFAULT,
                label or f"Porthole ({host_id})", password, None)
        except Exception as e:
            raise VaultError(f"Keychain store failed: {e}") from e
        if not ok:
            raise VaultError("The keychain refused to store the password.")

    def delete(self, host_id: str) -> None:
        try:
            self._Secret.password_clear_sync(self._schema, {"host_id": host_id}, None)
            # Forget the pre-rename copy too, or the fallback in get() would
            # bring the password back.
            if self._legacy_schema is not None:
                self._Secret.password_clear_sync(self._legacy_schema, {"host_id": host_id}, None)
        except Exception as e:
            raise VaultError(f"Keychain delete failed: {e}") from e

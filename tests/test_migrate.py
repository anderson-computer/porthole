"""3.0.0 rename: first-run copy of MultiSSH Manager 2.x data, legacy vault."""
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.env = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME")}
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.d, "cfg")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.d, "state")
        import porthole.models, porthole.migrate
        importlib.reload(porthole.models)
        self.m = importlib.reload(porthole.migrate)

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _legacy(self):
        old = Path(self.d, "cfg", "multissh-manager"); old.mkdir(parents=True)
        (old / "sessions.json").write_text(json.dumps({"format": "multissh-manager", "version": 2,
            "root": {"id": "root", "name": "Sessions", "folders": [], "hosts": [
                {"id": "a1", "name": "web", "hostname": "10.0.0.1", "username": "mike"}]}}))
        (old / "vault.json").write_text("{}")
        shared = Path(self.d, "share", "team.json")
        (old / "settings.json").write_text(json.dumps({
            "data_file": str(old / "sessions.json"), "vault_file": "",
            "log_file": str(Path(self.d, "state", "multissh-manager", "activity.log")),
            "font": "Monospace 13", "password_backend": "keyring"}))
        st = Path(self.d, "state", "multissh-manager"); st.mkdir(parents=True)
        (st / "activity.log").write_text("old log\n")
        return old, shared

    def test_copies_and_keeps_originals(self):
        old, _ = self._legacy()
        before = {p.name: p.read_bytes() for p in old.iterdir()}
        msg = self.m.migrate_legacy()
        self.assertIn("MultiSSH Manager", msg)
        new = Path(self.d, "cfg", "porthole")
        for name in ("sessions.json", "vault.json", "settings.json", self.m.MARKER):
            self.assertTrue((new / name).is_file(), name)
        self.assertEqual({p.name: p.read_bytes() for p in old.iterdir()}, before)  # untouched
        s = json.loads((new / "settings.json").read_text())
        self.assertEqual(s["data_file"], str(new / "sessions.json"))
        self.assertEqual(s["log_file"], str(Path(self.d, "state", "porthole", "activity.log")))
        self.assertEqual((s["font"], s["password_backend"]), ("Monospace 13", "keyring"))
        self.assertEqual(Path(self.d, "state", "porthole", "activity.log").read_text(), "old log\n")
        self.assertEqual(oct(os.stat(new).st_mode & 0o777), "0o700")
        from porthole.models import SessionStore
        self.assertEqual(next(SessionStore.load(new / "sessions.json").root.walk_hosts()).target,
                         "mike@10.0.0.1")
        # second run is a no-op
        self.assertIsNone(self.m.migrate_legacy())

    def test_outside_paths_untouched_and_no_legacy(self):
        self.assertIsNone(self.m.migrate_legacy())  # nothing to migrate
        old, shared = self._legacy()
        s = json.loads((old / "settings.json").read_text()); s["data_file"] = str(shared)
        (old / "settings.json").write_text(json.dumps(s))
        self.m.migrate_legacy()
        new = Path(self.d, "cfg", "porthole")
        self.assertEqual(json.loads((new / "settings.json").read_text())["data_file"], str(shared))

    def test_existing_porthole_dir_wins(self):
        self._legacy()
        Path(self.d, "cfg", "porthole").mkdir(parents=True)
        self.assertIsNone(self.m.migrate_legacy())
        self.assertFalse(Path(self.d, "cfg", "porthole", "sessions.json").exists())


class LegacyVaultTests(unittest.TestCase):
    def test_multissh_vault_opens_and_upgrades(self):
        from porthole.secret_store import FileVault
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.json"

            class OldVault(FileVault):
                FORMAT = "multissh-manager-vault"   # what 2.2.0 wrote
            OldVault.SCRYPT_N = FileVault.SCRYPT_N
            o = OldVault(p); o.create("master-pass-123"); o.set("h1", "s3cret")
            self.assertEqual(json.loads(p.read_text())["format"], "multissh-manager-vault")
            v = FileVault(p); v.unlock("master-pass-123")
            self.assertEqual(v.get("h1"), "s3cret")
            v.set("h2", "x")
            self.assertEqual(json.loads(p.read_text())["format"], "porthole-vault")
            v2 = FileVault(p); v2.unlock("master-pass-123")
            self.assertEqual((v2.get("h1"), v2.get("h2")), ("s3cret", "x"))


if __name__ == "__main__":
    unittest.main()

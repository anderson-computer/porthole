import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multissh_manager.models import Host  # noqa: E402
from multissh_manager.secret_store import FileVault, VaultError  # noqa: E402


class FileVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "v.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_and_no_plaintext(self):
        v = FileVault(self.path)
        v.create("correct horse")
        v.set("host_id-1", "Hunter2-secret")
        raw = self.path.read_text()
        # "_" and "-" are outside the base64 alphabet, so the host id can
        # never appear by chance inside the random salt/nonce/ciphertext.
        self.assertNotIn("Hunter2", raw)
        self.assertNotIn("host_id-1", raw)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        v2 = FileVault(self.path)
        v2.unlock("correct horse")
        self.assertEqual(v2.get("host_id-1"), "Hunter2-secret")
        v2.delete("host_id-1")
        self.assertIsNone(v2.get("host_id-1"))

    def test_wrong_password(self):
        FileVault(self.path).create("correct horse")
        with self.assertRaises(VaultError):
            FileVault(self.path).unlock("wrong horse")

    def test_tamper_detected(self):
        v = FileVault(self.path)
        v.create("correct horse")
        v.set("h1", "x")
        blob = json.loads(self.path.read_text())
        ct = bytearray(__import__("base64").b64decode(blob["ciphertext"]))
        ct[0] ^= 1
        blob["ciphertext"] = __import__("base64").b64encode(bytes(ct)).decode()
        self.path.write_text(json.dumps(blob))
        with self.assertRaises(VaultError):
            FileVault(self.path).unlock("correct horse")

    def test_locked_and_short_master(self):
        with self.assertRaises(VaultError):
            FileVault(self.path).create("short")
        v = FileVault(self.path)
        v.create("correct horse")
        v.lock()
        with self.assertRaises(VaultError):
            v.get("h1")

    def test_change_master(self):
        v = FileVault(self.path)
        v.create("correct horse")
        v.set("h1", "pw")
        v.change_master("correct horse", "battery staple")
        with self.assertRaises(VaultError):
            FileVault(self.path).unlock("correct horse")
        v3 = FileVault(self.path)
        v3.unlock("battery staple")
        self.assertEqual(v3.get("h1"), "pw")


class HostFlagTests(unittest.TestCase):
    def test_clone_does_not_copy_saved_flag(self):
        h = Host(hostname="x", save_password=True)
        self.assertFalse(h.clone().save_password)
        self.assertNotIn("password\"", json.dumps(h.to_dict()).replace("save_password", ""))


class AskpassTests(unittest.TestCase):
    def test_helper_round_trip(self):
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        from multissh_manager.askpass_server import HELPER, AskpassServer

        with tempfile.TemporaryDirectory() as d:
            os.environ["XDG_RUNTIME_DIR"] = d
            srv = AskpassServer()
            srv.start()
            seen = []
            token = srv.register(lambda p, k: seen.append((p, k)) or "s3cret")
            env = dict(os.environ)
            env.update(kv.split("=", 1) for kv in srv.env_for(token))
            loop = GLib.MainLoop()
            out = {}

            def run_helper(tok):
                e = dict(env, MSM_ASKPASS_TOKEN=tok)
                return subprocess.run([str(HELPER), "bob@h's password: "], env=e,
                                      capture_output=True, text=True, timeout=10)

            def worker():
                out["good"] = run_helper(token)
                out["bad"] = run_helper("not-a-token")
                GLib.idle_add(loop.quit)

            threading.Thread(target=worker, daemon=True).start()
            loop.run()
            srv.stop()
            self.assertEqual(out["good"].returncode, 0)
            self.assertEqual(out["good"].stdout, "s3cret\n")
            self.assertEqual(seen, [("bob@h's password: ", "")])
            self.assertNotEqual(out["bad"].returncode, 0)
            self.assertEqual(out["bad"].stdout, "")
            self.assertEqual(oct(os.stat(Path(d) / "multissh-manager").st_mode & 0o777), "0o700")


if __name__ == "__main__":
    unittest.main()

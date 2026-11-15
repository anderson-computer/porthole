"""2.3.0: username field, user@host parsing, alias keys, CLI parsing."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from porthole import cli  # noqa: E402
from porthole.models import (Host, SessionStore, parse_target,  # noqa: E402
                                     ssh_config_user)


class ParseTargetTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_target("admin@10.0.0.5:2222"), ("admin", "10.0.0.5", 2222))
        self.assertEqual(parse_target("admin@10.0.0.5"), ("admin", "10.0.0.5", None))
        self.assertEqual(parse_target("10.0.0.5"), ("", "10.0.0.5", None))
        self.assertEqual(parse_target("u@[fe80::1]:22"), ("u", "fe80::1", 22))
        self.assertEqual(parse_target("fe80::1"), ("", "fe80::1", None))
        self.assertEqual(parse_target("a@b@host"), ("a@b", "host", None))
        with self.assertRaises(ValueError):
            parse_target("h:70000")


class UsernameTargetTests(unittest.TestCase):
    def test_target_and_argv_include_user(self):
        h = Host(hostname="192.168.64.21", username="mike", auth_method="password")
        self.assertEqual(h.build_argv()[-1], "mike@192.168.64.21")
        self.assertFalse(h.needs_username)

    def test_missing_user_flagged_and_override(self):
        h = Host(hostname="10.1.1.1", auth_method="password")
        self.assertTrue(h.needs_username)
        self.assertEqual(h.build_argv("bob")[-1], "bob@10.1.1.1")
        self.assertEqual(h.target_for("bob"), "bob@10.1.1.1")
        self.assertEqual(h.target, "10.1.1.1")
        # a saved username always wins over a one-off one
        h.username = "alice"
        self.assertEqual(h.build_argv("bob")[-1], "alice@10.1.1.1")

    def test_telnet_never_needs_user(self):
        self.assertFalse(Host(hostname="h", protocol="telnet").needs_username)

    def test_alias_keys_from_old_files(self):
        for key in ("user", "login", "userid", "UserName"):
            h = Host.from_dict({"hostname": "h", key: "root"})
            self.assertEqual(h.username, "root", key)
        h = Host.from_dict({"host": "10.0.0.9", "user": "ops", "key_file": "~/.ssh/k"})
        self.assertEqual((h.hostname, h.username, h.private_key_path), ("10.0.0.9", "ops", "~/.ssh/k"))
        # canonical key wins over an alias
        self.assertEqual(Host.from_dict({"hostname": "h", "username": "a", "user": "b"}).username, "a")

    def test_user_at_host_in_hostname_field(self):
        h = Host.from_dict({"hostname": "ops@10.0.0.7:2200", "port": 22})
        self.assertEqual((h.username, h.hostname, h.port), ("ops", "10.0.0.7", 2200))
        self.assertEqual(h.build_argv(), ["ssh", "-p", "2200", "ops@10.0.0.7"])

    def test_username_round_trips_through_store(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            st = SessionStore()
            st.root.hosts.append(Host(name="x", hostname="x", username="mike"))
            st.save(p)
            self.assertEqual(json.loads(p.read_text())["root"]["hosts"][0]["username"], "mike")
            self.assertEqual(next(SessionStore.load(p).root.walk_hosts()).target, "mike@x")

    def test_sonar_session_file(self):
        # Exactly what Sonar 1.0.0 launchers.write_multissh_session writes.
        sess = {"format": "multissh-manager", "version": 2,
                "root": {"id": "root", "name": "Sessions", "folders": [{
                    "id": "abc", "name": "Sonar", "folders": [], "hosts": [{
                        "id": "def", "name": "10.0.0.6", "hostname": "10.0.0.6", "port": 22,
                        "username": "admin", "protocol": "ssh", "auth_method": "default",
                        "private_key_path": "", "jump_host": "", "post_login_command": "",
                        "extra_ssh_args": "", "notes": "Added by Sonar",
                        "save_password": False}]}], "hosts": []}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sonar-10.0.0.6.json"
            p.write_text(json.dumps(sess))
            h = next(SessionStore.load(p).root.walk_hosts())
            self.assertEqual(h.build_argv(), ["ssh", "admin@10.0.0.6"])

    def test_flat_converter_list(self):
        data = {"sessions": [
            {"name": "web", "folder": "Site A/Linux", "hostname": "web.test", "port": 22,
             "username": "deploy", "protocol": "SSH", "key_file": None},
            {"name": "db", "folder": "Site A", "hostname": "db.test", "port": 2228,
             "user": "oracle", "protocol": "SSH"}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "conv.json"
            p.write_text(json.dumps(data))
            st = SessionStore.load(p)
            self.assertIsNone(st.path)  # never auto-saved over the source file
            targets = sorted(h.target for h in st.root.walk_hosts())
            self.assertEqual(targets, ["deploy@web.test", "oracle@db.test"])

    def test_ssh_config_user(self):
        import shutil
        self.assertIsNone(ssh_config_user("-oProxyCommand=x"))
        if not shutil.which("ssh"):
            self.skipTest("no ssh client")
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config"
            cfg.write_text("Host cfghost\n  User cfguser\n")
            self.assertEqual(ssh_config_user("cfghost", 22, str(cfg)), "cfguser")
            self.assertIsNone(ssh_config_user("otherhost", 22, str(cfg)))


class CliTests(unittest.TestCase):
    def parse(self, *a):
        return cli.hosts_from_args(cli.build_parser().parse_args(list(a)))

    def test_connect_forms(self):
        h, = self.parse("--connect", "admin@10.0.0.5:2222")
        self.assertEqual((h.username, h.hostname, h.port), ("admin", "10.0.0.5", 2222))
        h, = self.parse("--host", "10.0.0.5", "--user", "admin", "--port", "2200")
        self.assertEqual(h.build_argv(), ["ssh", "-p", "2200", "admin@10.0.0.5"])
        h, = self.parse("--connect", "10.0.0.5")
        self.assertTrue(h.needs_username)
        h, = self.parse("--connect", "@10.0.0.5:22")  # Sonar with blank {user}
        self.assertTrue(h.needs_username)
        h, = self.parse("-c", "u@h", "--auth", "password")
        self.assertIn("PubkeyAuthentication=no", h.build_argv())
        h, = self.parse("-c", "u@h", "-i", "~/.ssh/id_x")
        self.assertEqual(h.auth_method, "key")
        self.assertEqual(len(self.parse("-c", "a@h1", "-c", "b@h2")), 2)
        a = cli.build_parser().parse_args(["sess.json", "-c", "u@h"])
        self.assertEqual(a.session_file, "sess.json")

    def test_rejects_bad_input(self):
        for args in (["--connect=-oProxyCommand=x"], ["--connect=-u@h"], ["--user", "x"],
                     ["-c", "h:99999"], ["--host", "h", "--port", "0"]):
            with self.assertRaises(ValueError, msg=args):
                self.parse(*args)


if __name__ == "__main__":
    unittest.main()

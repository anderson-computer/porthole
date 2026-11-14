import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multissh_manager import importer, ssh_config  # noqa: E402
from multissh_manager.models import Folder, Host, SessionStore  # noqa: E402


class HostArgvTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(Host(hostname="h").build_argv(), ["ssh", "h"])

    def test_port_user_key_jump(self):
        h = Host(hostname="h", port=2222, username="u", auth_method="key",
                 private_key_path="/k", jump_host="b@bastion")
        self.assertEqual(h.build_argv(), [
            "ssh", "-p", "2222", "-i", "/k", "-o", "IdentitiesOnly=yes",
            "-J", "b@bastion", "u@h"])

    def test_extra_args_respect_quoting(self):
        h = Host(hostname="h", extra_ssh_args='-o "ProxyCommand=nc -x p:1080 %h %p"')
        self.assertEqual(h.build_argv(),
                         ["ssh", "-o", "ProxyCommand=nc -x p:1080 %h %p", "h"])

    def test_password_only(self):
        argv = Host(hostname="h", auth_method="password").build_argv()
        self.assertIn("PubkeyAuthentication=no", argv)

    def test_telnet(self):
        self.assertEqual(Host(hostname="h", protocol="telnet", port=23).build_argv(),
                         ["telnet", "h"])
        self.assertEqual(Host(hostname="h", protocol="telnet", port=2323).build_argv(),
                         ["telnet", "h", "2323"])

    def test_v1_host_migrates(self):
        h = Host.from_dict({"hostname": "h", "protocol": "raw", "auth_method": "bogus",
                            "port": "2200"})
        self.assertEqual((h.protocol, h.auth_method, h.port), ("ssh", "default", 2200))


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.root = Folder(id="root")
        self.a = Folder(name="a")
        self.b = Folder(name="b")
        self.a.folders.append(self.b)
        self.h = Host(name="h", hostname="h")
        self.b.hosts.append(self.h)
        self.root.folders.append(self.a)

    def test_move_host(self):
        self.assertTrue(self.root.move(self.h.id, "root"))
        self.assertIn(self.h, self.root.hosts)
        self.assertNotIn(self.h, self.b.hosts)

    def test_refuse_move_into_descendant(self):
        self.assertFalse(self.root.move(self.a.id, self.b.id))
        self.assertIn(self.a, self.root.folders)

    def test_remove_and_count(self):
        self.assertEqual(self.root.count(), (2, 1))
        self.root.remove_folder(self.a.id)
        self.assertEqual(self.root.count(), (0, 0))


class StoreTests(unittest.TestCase):
    def test_roundtrip_and_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            store = SessionStore()
            store.root.hosts.append(Host(name="x", hostname="x"))
            store.save(p)
            store.save(p)
            self.assertTrue((Path(d) / "s.json.bak").exists())
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
            loaded = SessionStore.load(p)
            self.assertEqual(loaded.root.hosts[0].hostname, "x")

    def test_loads_v1_file(self):
        v1 = {"format": "multissh-manager", "version": 1, "root": {
            "id": "root", "name": "Sessions", "folders": [], "hosts": [
                {"id": "abc", "name": "old", "hostname": "o", "port": 22,
                 "username": "", "protocol": "ssh", "auth_method": "password",
                 "private_key_path": "", "post_login_command": "",
                 "extra_ssh_args": "", "notes": ""}]}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v1.json"
            p.write_text(json.dumps(v1))
            h = SessionStore.load(p).root.hosts[0]
            self.assertEqual(h.name, "old")
            self.assertEqual(h.jump_host, "")


class ImporterTests(unittest.TestCase):
    def _write(self, text, suffix=".xml"):
        fd, p = tempfile.mkstemp(suffix=suffix)
        os.write(fd, text.encode())
        os.close(fd)
        self.addCleanup(os.unlink, p)
        return p

    def test_puttycm_style(self):
        p = self._write("""<?xml version="1.0"?>
<configuration><root name="DB">
  <container name="Prod">
    <connection name="web1" HostName="10.0.0.1" Port="2222" Username="admin"/>
    <container name="DB"><connection name="db1" HostName="10.0.0.2"/></container>
  </container>
</root></configuration>""")
        top = importer.parse_file(p)
        self.assertEqual(top.folders[0].name, "DB")
        prod = top.folders[0].folders[0]
        self.assertEqual(prod.name, "Prod")
        self.assertEqual(prod.hosts[0].port, 2222)
        self.assertEqual(prod.folders[0].hosts[0].hostname, "10.0.0.2")

    def test_child_element_fields(self):
        p = self._write("<Sessions><Session><Name>n</Name><Host>h</Host></Session></Sessions>",
                        ".dat")
        top = importer.parse_file(p)
        self.assertEqual(top.hosts[0].name, "n")

    def test_no_hosts_raises(self):
        p = self._write("<root><folder name='x'/></root>")
        with self.assertRaises(ValueError):
            importer.parse_file(p)

    def test_not_xml(self):
        p = self._write("{}", ".json")
        self.assertFalse(importer.looks_like_multiputty_file(p))


class SshConfigTests(unittest.TestCase):
    def test_parse(self):
        with tempfile.TemporaryDirectory() as d:
            extra = Path(d) / "extra.conf"
            extra.write_text("Host inc\n  HostName 1.2.3.4\n")
            cfg = Path(d) / "config"
            cfg.write_text(
                f"Include {extra}\n"
                "Host web1 web2\n  HostName web.example.com\n  User deploy\n"
                "Host *.internal !bad\n  User x\n"
                "Host db\n  ProxyJump bastion\n")
            folder = ssh_config.parse_ssh_config(cfg)
            names = [h.name for h in folder.hosts]
            self.assertEqual(names, ["inc", "web1", "web2", "db"])
            self.assertIn("User deploy", folder.hosts[1].notes)
            self.assertEqual(folder.hosts[1].build_argv(), ["ssh", "web1"])
            self.assertIn("ProxyJump bastion", folder.hosts[3].notes)


if __name__ == "__main__":
    unittest.main()

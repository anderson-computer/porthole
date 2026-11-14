import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multissh_manager.activity_log import ActivityLog, redact  # noqa: E402


class RedactTests(unittest.TestCase):
    def check(self, text, secret):
        out = redact(text)
        self.assertNotIn(secret, out, out)
        self.assertIn("***", out)

    def test_patterns(self):
        self.check("mysql -u root -pHunter2 db", "Hunter2")
        self.check("sshpass -p 's3cr3t' ssh host", "s3cr3t")
        self.check("echo Hunter2 | sudo -S reboot", "Hunter2")
        self.check("curl -H 'Authorization: Bearer abc.def.ghi' x", "abc.def.ghi")
        self.check("export DB_PASSWORD=Hunter2", "Hunter2")
        self.check("app --password Hunter2 --user bob", "Hunter2")
        self.check("git clone https://bob:Hunter2@example.com/r.git", "Hunter2")
        self.check("API_KEY: Hunter2", "Hunter2")

    def test_leaves_ordinary_commands(self):
        for cmd in ("uptime", "df -h /", "sudo systemctl restart nginx", "ls -la /etc",
                    "ssh -p 2222 root@10.0.0.1", "mysql -u root -p db"):
            self.assertEqual(redact(cmd), cmd)


class ActivityLogTests(unittest.TestCase):
    def test_append_perms_and_quoting(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "a.log"
            log = ActivityLog()
            self.assertIsNone(log.configure(True, str(p)))
            log.log("session", "connect", host='we"b', note="a\nb", empty="")
            log2 = ActivityLog()
            log2.configure(True, str(p))
            log2.log("app", "quit")
            lines = p.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn('host="we\\"b"', lines[0])
            self.assertIn('note="a\\nb"', lines[0])
            self.assertNotIn("empty=", lines[0])
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")

    def test_disabled_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.log"
            log = ActivityLog()
            log.configure(False, str(p))
            log.log("app", "start")
            self.assertFalse(p.exists())

    def test_unwritable_reports_error(self):
        log = ActivityLog()
        err = log.configure(True, "/proc/nope/a.log")
        self.assertTrue(err)
        self.assertFalse(log.enabled)


class SettingsPersistTests(unittest.TestCase):
    def test_options_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["XDG_CONFIG_HOME"] = d
            try:
                import multissh_manager.models as m
                import multissh_manager.settings as s
                importlib.reload(m)
                importlib.reload(s)
                st = s.Settings.load()
                self.assertEqual(st.data_path, Path(d) / "multissh-manager" / "sessions.json")
                st.data_file = str(Path(d) / "elsewhere" / "mine.json")
                st.log_enabled = True
                st.log_file = str(Path(d) / "act.log")
                self.assertIsNone(st.save())
                again = s.Settings.load()
                self.assertEqual(again.data_path, Path(d) / "elsewhere" / "mine.json")
                self.assertTrue(again.log_enabled)
                self.assertEqual(again.log_file, str(Path(d) / "act.log"))
            finally:
                del os.environ["XDG_CONFIG_HOME"]
                importlib.reload(m)
                importlib.reload(s)


if __name__ == "__main__":
    unittest.main()

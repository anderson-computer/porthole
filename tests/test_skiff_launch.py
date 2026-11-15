"""Open in Skiff: finding Skiff, argv, and the pipe password handoff."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from porthole import skiff_launch as SL  # noqa: E402
from porthole.models import Host  # noqa: E402

FAKE = r'''#!/usr/bin/env python3
# Stand-in for skiff: records argv, env and what it reads from --password-fd.
import json, os, sys
if "--help" in sys.argv:
    print("usage: skiff [--host HOST] [--user USER] [--port PORT]%s" % os.environ.get("FAKE_EXTRA", "@@EXTRA@@"))
    sys.exit(0)
out = {"argv": sys.argv, "env_has_pw": any("Pw-Secret" in v for v in os.environ.values())}
if "--password-fd" in sys.argv:
    fd = int(sys.argv[sys.argv.index("--password-fd") + 1])
    data = b""
    while True:
        c = os.read(fd, 4096)
        if not c:
            break
        data += c
    out["pw"] = data.decode()
open(os.environ["FAKE_OUT"], "w").write(json.dumps(out))
'''


class SkiffLaunchTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.out = os.path.join(self.d, "out.json")
        os.environ["FAKE_OUT"] = self.out

    def fake(self, extra):
        p = os.path.join(self.d, "skiff-%d" % len(os.listdir(self.d)))
        Path(p).write_text(FAKE.replace("@@EXTRA@@", extra))
        os.chmod(p, 0o755)
        return p

    def run_launch(self, exe, host, password=None, user=""):
        procs = []

        def popen(argv, **kw):
            p = subprocess.Popen(argv, **kw)
            procs.append(p)
            return p
        argv, notes = SL.launch(exe, host, user, password, popen=popen)
        procs[0].wait(timeout=20)
        return argv, notes, json.loads(Path(self.out).read_text())

    def test_find_skiff(self):
        exe = self.fake("")
        self.assertEqual(SL.find_skiff(exe), exe)
        self.assertNotEqual(SL.find_skiff("/nonexistent/skiff"), "/nonexistent/skiff")

    def test_password_goes_through_pipe_only(self):
        exe = self.fake(" [--key PATH] [--password-fd N]")
        h = Host(hostname="10.0.0.5", port=2222, username="mike", auth_method="password")
        argv, notes, rec = self.run_launch(exe, h, "Pw-Secret 1")
        self.assertEqual(rec["pw"], "Pw-Secret 1")
        self.assertFalse(rec["env_has_pw"])
        self.assertNotIn("Pw-Secret", " ".join(rec["argv"]))
        self.assertEqual(rec["argv"][1:7], ["--host", "10.0.0.5", "--user", "mike", "--port", "2222"])
        self.assertEqual(rec["argv"][7], "--password-fd")
        self.assertEqual(notes, [])

    def test_no_fd_leak(self):
        exe = self.fake(" [--key PATH] [--password-fd N]")
        before = set(os.listdir("/proc/self/fd"))
        self.run_launch(exe, Host(hostname="h", username="u"), "Pw-Secret 3")
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)

    def test_key_passed(self):
        exe = self.fake(" [--key PATH] [--password-fd N]")
        h = Host(hostname="h", username="u", auth_method="key", private_key_path="~/.ssh/id_x")
        argv, notes, rec = self.run_launch(exe, h)
        self.assertEqual(rec["argv"][-2:], ["--key", os.path.expanduser("~/.ssh/id_x")])
        self.assertNotIn("pw", rec)

    def test_old_skiff_never_gets_password(self):
        exe = self.fake("")  # 1.3.0 without --key/--password-fd
        h = Host(hostname="h", username="u", auth_method="key", private_key_path="/k")
        argv, notes, rec = self.run_launch(exe, h, "Pw-Secret 2")
        self.assertNotIn("--password-fd", rec["argv"])
        self.assertNotIn("--key", rec["argv"])
        self.assertTrue(any("--password-fd" in n for n in notes) and any("--key" in n for n in notes), notes)

    def test_skiff_without_cli_is_refused(self):
        p = os.path.join(self.d, "oldskiff")
        Path(p).write_text("#!/bin/sh\necho 'usage: skiff [Qt options]'\n")
        os.chmod(p, 0o755)
        with self.assertRaises(RuntimeError):
            SL.launch(p, Host(hostname="h"), popen=lambda *a, **k: None)

    def test_user_override_and_defaults(self):
        exe = self.fake(" [--key PATH] [--password-fd N]")
        caps = SL.skiff_capabilities(exe)
        argv, _ = SL.build_argv(exe, Host(hostname="h"), caps, user="asked")
        self.assertEqual(argv[1:], ["--host", "h", "--user", "asked"])
        argv, _ = SL.build_argv(exe, Host(hostname="h", username="saved"), caps, user="asked")
        self.assertEqual(argv[1:], ["--host", "h", "--user", "saved"])


if __name__ == "__main__":
    unittest.main()

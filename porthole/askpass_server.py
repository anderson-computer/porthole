"""
In-process server that answers the askpass helper (askpass.py).

Each terminal tab registers a random one-time token and a handler. ssh is
started with the socket path and that token in its environment; when it
needs a password it runs the helper, which sends {token, prompt, kind}
here. The handler runs on the GTK main thread and returns the answer (or
None to refuse). Connections from other users are rejected by peer
credentials, and the socket lives in a 0700 directory under
$XDG_RUNTIME_DIR.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import struct
import tempfile
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

from gi.repository import GLib

HELPER = Path(__file__).resolve().parent / "askpass.py"

Handler = Callable[[str, str], Optional[str]]


class AskpassServer:
    def __init__(self):
        self._handlers: Dict[str, Handler] = {}
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self.path: Optional[Path] = None
        self._dir: Optional[Path] = None

    def start(self) -> None:
        if self._sock is not None:
            return
        base = os.environ.get("XDG_RUNTIME_DIR")
        if base and os.path.isdir(base):
            d = Path(base) / "porthole"
            d.mkdir(mode=0o700, exist_ok=True)
        else:
            d = Path(tempfile.mkdtemp(prefix="porthole-"))
        os.chmod(d, 0o700)
        self._dir = d
        self.path = d / f"askpass-{os.getpid()}.sock"
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(self.path))
        os.chmod(self.path, 0o600)
        s.listen(8)
        self._sock = s
        threading.Thread(target=self._accept_loop, name="askpass", daemon=True).start()

    def stop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self.path is not None:
            try:
                self.path.unlink()
            except OSError:
                pass

    def register(self, handler: Handler) -> str:
        token = secrets.token_hex(24)
        with self._lock:
            self._handlers[token] = handler
        return token

    def unregister(self, token: Optional[str]) -> None:
        if token:
            with self._lock:
                self._handlers.pop(token, None)

    def env_for(self, token: str):
        return [
            f"SSH_ASKPASS={HELPER}",
            "SSH_ASKPASS_REQUIRE=force",
            f"MSM_ASKPASS_SOCK={self.path}",
            f"MSM_ASKPASS_TOKEN={token}",
        ]

    # ------------------------------------------------------------------
    def _accept_loop(self) -> None:
        while self._sock is not None:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            try:
                creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                        struct.calcsize("3i"))
                _pid, uid, _gid = struct.unpack("3i", creds)
                if uid != os.getuid():
                    return
                raw = b""
                while len(raw) < 65536:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                req = json.loads(raw.decode("utf-8"))
                with self._lock:
                    handler = self._handlers.get(str(req.get("token", "")))
                if handler is None:
                    conn.sendall(b'{"ok": false}')
                    return
                answer = self._call_on_main(handler, str(req.get("prompt", "")),
                                            str(req.get("kind", "")))
                reply = {"ok": answer is not None, "answer": answer or ""}
                conn.sendall(json.dumps(reply).encode("utf-8"))
            except (OSError, ValueError):
                return

    @staticmethod
    def _call_on_main(handler: Handler, prompt: str, kind: str) -> Optional[str]:
        done = threading.Event()
        box = {}

        def run():
            try:
                box["answer"] = handler(prompt, kind)
            except Exception:  # never leave ssh hanging
                box["answer"] = None
            done.set()
            return False

        GLib.idle_add(run)
        done.wait()
        return box.get("answer")

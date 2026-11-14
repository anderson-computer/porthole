#!/usr/bin/python3 -I
"""
SSH_ASKPASS helper for MultiSSH Manager.

ssh runs this program (with SSH_ASKPASS_REQUIRE=force) whenever it needs a
password, passphrase or yes/no answer. It forwards the prompt to the
running MultiSSH Manager over a private Unix socket and prints the answer
for ssh to read. It never stores anything.
"""
import json
import os
import socket
import sys


def main() -> int:
    sock_path = os.environ.get("MSM_ASKPASS_SOCK")
    token = os.environ.get("MSM_ASKPASS_TOKEN")
    if not sock_path or not token:
        sys.stderr.write("multissh-manager askpass: not started by MultiSSH Manager\n")
        return 1
    request = {
        "token": token,
        "prompt": sys.argv[1] if len(sys.argv) > 1 else "",
        "kind": os.environ.get("SSH_ASKPASS_PROMPT", ""),  # "", "confirm" or "none"
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(sock_path)
            s.sendall(json.dumps(request).encode("utf-8") + b"\n")
            s.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                data = s.recv(65536)
                if not data:
                    break
                chunks.append(data)
        reply = json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, ValueError) as e:
        sys.stderr.write(f"multissh-manager askpass: {e}\n")
        return 1
    if not reply.get("ok"):
        return 1
    sys.stdout.write(reply.get("answer", "") + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

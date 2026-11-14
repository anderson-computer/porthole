#!/usr/bin/env python3
"""Entry point: launch MultiSSH Manager from a source checkout."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from multissh_manager.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

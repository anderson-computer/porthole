#!/usr/bin/env bash
# Build the porthole SRPM + noarch RPM inside a fresh Rocky Linux 9
# or 10 container (or chroot) with the source tree mounted at /work
# (override with WORK=...). Output goes to <source tree>/out/.
set -euo pipefail
dnf -y -q install rpm-build python3 python3-cryptography python3-gobject-base desktop-file-utils tar gzip
WORK="${WORK:-/work}"
bash "${WORK}/packaging/build-rpm.sh"

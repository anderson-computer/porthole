#!/usr/bin/env bash
# Install (or uninstall) Porthole on Rocky Linux 9 / 10.
#   sudo ./install.sh             install or upgrade
#   sudo ./install.sh --uninstall remove (your sessions in ~/.config are kept)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Re-running with sudo (needed to install system packages)." >&2
    exec sudo -E bash "$0" "$@"
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/porthole"
BIN="/usr/local/bin/porthole"
DESKTOP="/usr/share/applications/porthole.desktop"
ICON="/usr/share/icons/hicolor/scalable/apps/porthole.svg"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "${INSTALL_DIR}" "${BIN}" "${DESKTOP}" "${ICON}"
    [ -L /usr/local/bin/multissh-manager ] && rm -f /usr/local/bin/multissh-manager
    gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
    echo "==> Removed. Your sessions in ~/.config/porthole were left in place."
    exit 0
fi

echo "==> Installing dependencies (GTK3, VTE, PyGObject, OpenSSH client)"
dnf install -y python3 python3-gobject gtk3 vte291 openssh-clients \
    python3-cryptography libsecret

echo "==> Installing application to ${INSTALL_DIR}"
rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
cp -r "${SRC}/porthole" "${SRC}/run.py" "${INSTALL_DIR}/"
find "${INSTALL_DIR}" -name __pycache__ -prune -exec rm -rf {} +

cat > "${BIN}" <<'EOF'
#!/bin/sh
exec python3 /opt/porthole/run.py "$@"
EOF
chmod 0755 "${BIN}"
# Old name from MultiSSH Manager 2.x, so existing scripts/launchers still work.
ln -sfn porthole /usr/local/bin/multissh-manager

install -Dm0644 "${SRC}/porthole.desktop" "${DESKTOP}"
install -Dm0644 "${SRC}/porthole/resources/logo.svg" "${ICON}"
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true

echo "==> Done. Launch from the applications menu, or run: porthole"

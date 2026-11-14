#!/usr/bin/env bash
# Install (or uninstall) MultiSSH Manager on Rocky Linux 9 / 10.
#   sudo ./install.sh             install or upgrade
#   sudo ./install.sh --uninstall remove (your sessions in ~/.config are kept)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Re-running with sudo (needed to install system packages)." >&2
    exec sudo -E bash "$0" "$@"
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/multissh-manager"
BIN="/usr/local/bin/multissh-manager"
DESKTOP="/usr/share/applications/multissh-manager.desktop"
ICON="/usr/share/icons/hicolor/scalable/apps/multissh-manager.svg"
CONFIG_DIR="/etc/multissh-manager"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "${INSTALL_DIR}" "${BIN}" "${DESKTOP}" "${ICON}"
    gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
    echo "==> Removed. ${CONFIG_DIR} and ~/.config/multissh-manager were left in place."
    exit 0
fi

echo "==> Installing dependencies (GTK3, VTE, PyGObject, OpenSSH client)"
dnf install -y python3 python3-gobject gtk3 vte291 openssh-clients

echo "==> Installing application to ${INSTALL_DIR}"
rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
cp -r "${SRC}/multissh_manager" "${SRC}/run.py" "${INSTALL_DIR}/"
find "${INSTALL_DIR}" -name __pycache__ -prune -exec rm -rf {} +

cat > "${BIN}" <<'EOF'
#!/bin/sh
exec python3 /opt/multissh-manager/run.py "$@"
EOF
chmod 0755 "${BIN}"

install -Dm0644 "${SRC}/multissh-manager.desktop" "${DESKTOP}"
install -Dm0644 "${SRC}/multissh_manager/resources/logo.svg" "${ICON}"
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true

mkdir -p "${CONFIG_DIR}"
# A 1.x install left its own about.conf here; move it aside so the
# new defaults apply.
if [ -f "${CONFIG_DIR}/about.conf" ] && grep -q "^version=1\." "${CONFIG_DIR}/about.conf"; then
    mv "${CONFIG_DIR}/about.conf" "${CONFIG_DIR}/about.conf.v1-backup"
    echo "==> Moved old ${CONFIG_DIR}/about.conf to about.conf.v1-backup"
fi
if [ ! -e "${CONFIG_DIR}/about.conf" ]; then
    install -m0644 "${SRC}/packaging/about.conf.system" "${CONFIG_DIR}/about.conf"
fi

echo "==> Done. Launch from the applications menu, or run: multissh-manager"

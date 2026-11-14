#!/usr/bin/env bash
# Builds the noarch multissh-manager RPM from this source tree.
# Needs only rpm-build from the stock Rocky repos; no network access.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
NAME="multissh-manager"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "${PROJECT_DIR}/multissh_manager/__init__.py")"

if ! command -v rpmbuild >/dev/null 2>&1; then
    echo "rpmbuild not found. Install it first: sudo dnf install -y rpm-build" >&2
    exit 1
fi

echo "==> Running unit tests"
python3 -m unittest discover -s "${PROJECT_DIR}/tests" -q

TOPDIR="$(mktemp -d)"
trap 'rm -rf "${TOPDIR}"' EXIT
mkdir -p "${TOPDIR}"/{SPECS,SOURCES,BUILD,RPMS,SRPMS}
SRC_DIR="${TOPDIR}/stage/${NAME}-${VERSION}"
mkdir -p "${SRC_DIR}"
cp -a "${PROJECT_DIR}"/{multissh_manager,run.py,multissh-manager.desktop,README.md,HELP.md} "${SRC_DIR}/"
cp -a "${PROJECT_DIR}/packaging/about.conf.system" "${SRC_DIR}/"
find "${SRC_DIR}" -name __pycache__ -prune -exec rm -rf {} +
tar -czf "${TOPDIR}/SOURCES/${NAME}-${VERSION}.tar.gz" -C "${TOPDIR}/stage" "${NAME}-${VERSION}"

echo "==> Building ${NAME} ${VERSION}"
rpmbuild --define "_topdir ${TOPDIR}" --define "app_version ${VERSION}" \
    -bb "${SCRIPT_DIR}/${NAME}.spec"

OUT="${PROJECT_DIR}/dist"
mkdir -p "${OUT}"
cp "${TOPDIR}"/RPMS/noarch/*.rpm "${OUT}/"
echo
echo "==> Done:"
ls -1 "${OUT}"/*.rpm
echo "    Install with: sudo dnf install ${OUT}/${NAME}-${VERSION}-*.rpm"

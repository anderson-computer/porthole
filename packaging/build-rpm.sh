#!/usr/bin/env bash
# Build multissh-manager RPMs (noarch binary RPM + source RPM).
#
#   ./packaging/build-rpm.sh          on Rocky/RHEL 9 or 10: native rpmbuild,
#                                     produces -1.el9 or -1.el10 for this host
#   ./packaging/build-rpm.sh 9 10     anywhere with podman or docker: builds in
#                                     rockylinux:9 and rockylinux:10 containers
#
# Output: out/multissh-manager-<ver>-1.elN.noarch.rpm
#         out/multissh-manager-<ver>-1.elN.src.rpm
#         out/multissh-manager-<ver>.tar.gz
# Native builds need: sudo dnf install -y rpm-build desktop-file-utils python3-cryptography python3-gobject-base
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
NAME="multissh-manager"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "${PROJECT_DIR}/multissh_manager/__init__.py")"
OUT="${PROJECT_DIR}/out"

if [ $# -gt 0 ]; then
    ENGINE="$(command -v podman || command -v docker || true)"
    [ -n "${ENGINE}" ] || { echo "podman or docker is required for container builds." >&2; exit 1; }
    for v in "$@"; do
        echo "==> Building in docker.io/rockylinux/rockylinux:${v}"
        "${ENGINE}" run --rm -v "${PROJECT_DIR}:/work:Z" "docker.io/rockylinux/rockylinux:${v}" \
            bash /work/packaging/build-in-container.sh
    done
    ls -lh "${OUT}"/${NAME}-*.rpm
    exit 0
fi

for tool in rpmbuild desktop-file-validate; do
    command -v "${tool}" >/dev/null 2>&1 || {
        echo "${tool} not found. Install: sudo dnf install -y rpm-build desktop-file-utils python3-cryptography python3-gobject-base" >&2
        echo "Or build in containers:    ./packaging/build-rpm.sh 9 10" >&2
        exit 1
    }
done

echo "==> Running unit tests"
(cd "${PROJECT_DIR}" && python3 -m unittest discover -s tests -q)

TOPDIR="$(mktemp -d)"
trap 'rm -rf "${TOPDIR}"' EXIT
mkdir -p "${TOPDIR}"/{SPECS,SOURCES,BUILD,BUILDROOT,RPMS,SRPMS} "${OUT}"
SRC_DIR="${TOPDIR}/stage/${NAME}-${VERSION}"
mkdir -p "${SRC_DIR}"
cp -a "${PROJECT_DIR}"/{multissh_manager,tests,packaging,run.py,install.sh,multissh-manager.desktop,README.md,HELP.md,LICENSE} "${SRC_DIR}/"
find "${SRC_DIR}" -name __pycache__ -prune -exec rm -rf {} +
find "${SRC_DIR}" -name '*.py[co]' -delete
tar -czf "${TOPDIR}/SOURCES/${NAME}-${VERSION}.tar.gz" -C "${TOPDIR}/stage" "${NAME}-${VERSION}"
sed 's/\r$//' "${SCRIPT_DIR}/${NAME}.spec" > "${TOPDIR}/SPECS/${NAME}.spec"

echo "==> Building ${NAME} ${VERSION}"
rpmbuild --define "_topdir ${TOPDIR}" --define "app_version ${VERSION}" \
    -ba "${TOPDIR}/SPECS/${NAME}.spec"

cp "${TOPDIR}"/RPMS/noarch/${NAME}-*.rpm "${TOPDIR}"/SRPMS/${NAME}-*.src.rpm \
   "${TOPDIR}/SOURCES/${NAME}-${VERSION}.tar.gz" "${OUT}/"
echo
echo "==> Done:"
ls -1 "${OUT}"/${NAME}-${VERSION}-*.rpm
echo "    Install with: sudo dnf install ${OUT}/${NAME}-${VERSION}-1.el\$(rpm -E %rhel).noarch.rpm"

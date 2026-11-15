%{!?app_version: %global app_version 2.2.0}
%global debug_package %{nil}

Name:           multissh-manager
Version:        %{app_version}
Release:        1%{?dist}
Summary:        Tabbed SSH session manager with folders and multi-session broadcast

License:        MIT
URL:            https://github.com/mike0615/multissh-manager-v2
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3
BuildRequires:  python3-cryptography
# GLib bindings for the askpass round-trip unit test (no GTK needed).
BuildRequires:  python3-gobject-base
BuildRequires:  desktop-file-utils

# Every runtime dependency ships in the stock Rocky Linux 9/10 BaseOS and
# AppStream repos (no EPEL, CRB or pip). Same package names on EL9 and EL10:
#   python3-gobject  PyGObject (gi) incl. GLib/Gio/GdkPixbuf bindings
#   gtk3             Gtk-3.0 / Gdk-3.0 typelibs
#   vte291           Vte-2.91 (GTK3 VTE terminal widget) typelib
#   libsecret        Secret-1 typelib (system keychain for saved passwords)
#   python3-cryptography  AES-256-GCM + scrypt for the encrypted vault
#   openssh-clients  ssh, which every session tab runs
Requires:       python3
Requires:       python3-gobject
Requires:       gtk3
Requires:       vte291
Requires:       openssh-clients
Requires:       python3-cryptography
Requires:       libsecret
# Only needed for hosts set to the telnet protocol.
Recommends:     telnet

%description
MultiSSH Manager is a native GTK tabbed SSH session manager for Rocky
Linux: a searchable folder tree of saved hosts, each connection in its own
tab running the system ssh client, per-tab broadcast with live keystroke
mirroring, reconnect-in-place, and import from MultiPuTTY / PuTTY CM XML
exports or ~/.ssh/config.

%prep
%setup -q
find . -name '__pycache__' -prune -exec rm -rf {} +
find . -name '*.py[co]' -delete

%build
# Pure Python application: nothing to compile.

%install
install -d %{buildroot}%{_datadir}/%{name}
cp -a multissh_manager run.py %{buildroot}%{_datadir}/%{name}/
# Ship (and own) the bytecode so running the app as root never leaves
# unowned __pycache__ files behind after "dnf remove".
python3 -m compileall -q -s %{buildroot} -p / %{buildroot}%{_datadir}/%{name}/multissh_manager

install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/%{name} <<'EOF'
#!/bin/sh
exec python3 %{_datadir}/%{name}/run.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}

desktop-file-install --dir=%{buildroot}%{_datadir}/applications %{name}.desktop
install -Dm0644 multissh_manager/resources/logo.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{name}.desktop
python3 -m unittest discover -s tests -q

%files
%{_datadir}/%{name}/
%{_bindir}/%{name}
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
%license LICENSE
%doc README.md HELP.md

%post
gtk-update-icon-cache -q %{_datadir}/icons/hicolor &>/dev/null || :

%postun
gtk-update-icon-cache -q %{_datadir}/icons/hicolor &>/dev/null || :

%changelog
* Sat Nov 14 2026 Mike Anderson <18270693+mike0615@users.noreply.github.com> - 2.2.0-1
- Saved host passwords in the system keychain (libsecret) or an AES-256-GCM
  encrypted vault file with a scrypt-derived master password.
- Passwords reach ssh via SSH_ASKPASS over a private socket; other ssh
  prompts (host keys, passphrases, OTP) appear as dialogs for those hosts.
- Options dialog split into tabs.
- Prebuilt noarch RPMs for Rocky Linux 9 (el9) and 10 (el10), plus SRPMs;
  desktop file validated and unit tests run at build time; bytecode is
  packaged so no unowned __pycache__ is left behind on removal.
- Recommend telnet for telnet-protocol hosts.

* Sat Nov 14 2026 Mike Anderson <18270693+mike0615@users.noreply.github.com> - 2.1.0-1
- Edit > Options (replaces File > Preferences): choose the session data
  file location; optional append-only activity log with secret redaction.
- Options are saved immediately and restored on restart.

* Sat Nov 14 2026 Mike Anderson <18270693+mike0615@users.noreply.github.com> - 2.0.0-3
- About tab: author, project page and website links; content is fixed.
- Drop the about.conf override files.

* Sat Nov 14 2026 Mike <18270693+mike0615@users.noreply.github.com> - 2.0.0-2
- Add MIT LICENSE file and license notice on the About tab.
- Install about.conf correctly when upgrading over a 1.x install.sh setup.

* Sat Nov 14 2026 Mike <18270693+mike0615@users.noreply.github.com> - 2.0.0-1
- Rewrite: per-tab broadcast targets and live keystroke mirroring,
  reconnect-in-place, sidebar search, drag-and-drop, auto-save,
  ~/.ssh/config import, jump hosts, preferences, neutral branding.
- Shortcuts moved to Ctrl+Shift so shell keys (Ctrl+W/O/S) work.
- Post-login commands wait for a shell prompt.
- Installs under /usr/share (noarch) instead of /usr/lib64.

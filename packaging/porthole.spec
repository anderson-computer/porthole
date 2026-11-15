%{!?app_version: %global app_version 3.1.0}
%global debug_package %{nil}

Name:           porthole
Version:        %{app_version}
Release:        1%{?dist}
Summary:        Tabbed SSH session manager with folders and multi-session broadcast

License:        MIT
URL:            https://github.com/mike0615/porthole
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

# Porthole was called MultiSSH Manager (package multissh-manager) up to 2.2.0.
# dnf upgrade replaces it; the multissh-manager command keeps working.
Obsoletes:      multissh-manager < 3.0.0
Provides:       multissh-manager = %{version}-%{release}

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
Porthole is a native GTK tabbed SSH session manager for Rocky
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
cp -a porthole run.py %{buildroot}%{_datadir}/%{name}/
# Ship (and own) the bytecode so running the app as root never leaves
# unowned __pycache__ files behind after "dnf remove".
python3 -m compileall -q -s %{buildroot} -p / %{buildroot}%{_datadir}/%{name}/porthole

install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/%{name} <<'EOF'
#!/bin/sh
exec python3 %{_datadir}/%{name}/run.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}
# Old command name (used by scripts and by Sonar's default multissh_path).
ln -s %{name} %{buildroot}%{_bindir}/multissh-manager

desktop-file-install --dir=%{buildroot}%{_datadir}/applications %{name}.desktop
install -Dm0644 porthole/resources/logo.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{name}.desktop
python3 -m unittest discover -s tests -q

%files
%{_datadir}/%{name}/
%{_bindir}/%{name}
%{_bindir}/multissh-manager
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
%license LICENSE
%doc README.md HELP.md

%post
gtk-update-icon-cache -q %{_datadir}/icons/hicolor &>/dev/null || :

%postun
gtk-update-icon-cache -q %{_datadir}/icons/hicolor &>/dev/null || :

%changelog
* Sat Nov 14 2026 Mike Anderson <mike@andersoncomputer.us> - 3.1.0-1
- Right-click a host > Open in Skiff: starts the Skiff SFTP client connected
  with the host's saved user, port, key (--key) and password. The password
  is passed through an inherited pipe (--password-fd), never argv, the
  environment or a file. Greyed out when Skiff isn't found.
- Edit > Options > Applications: Skiff location (default /usr/bin/skiff).

* Sat Nov 14 2026 Mike Anderson <mike@andersoncomputer.us> - 3.0.0-1
- Renamed from MultiSSH Manager to Porthole: package porthole, command
  /usr/bin/porthole (multissh-manager kept as a symlink), new icon,
  config in ~/.config/porthole. Obsoletes multissh-manager < 3.0.0.
- First run copies ~/.config/multissh-manager (settings, sessions, vault)
  and the activity log to the new locations, leaving the originals; saved
  keychain passwords are read from the old entries and copied forward.
- Fix: the Add/Edit Host form now has its own labelled Username row; in
  2.2.0 it sat unlabelled beside Port and was easy to miss, so hosts were
  saved without a user and ssh connected as the local login.
- Connecting to an ssh host with no saved username asks for one
  (prefilled with the local login, optional "save for this host"),
  unless ~/.ssh/config sets a User for it.
- user@host and host:port typed into Host / IP are split into the fields.
- Session files using other key names (user, login, host, key_file...) and
  flat converter lists ({"sessions": [...]}) load with the user intact.
- New command line: --connect [USER@]HOST[:PORT] (repeatable), --host,
  --user, --port, --auth, -i, --name; opens a tab and connects at once.

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

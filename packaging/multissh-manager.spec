%{!?app_version: %global app_version 2.0.0}

Name:           multissh-manager
Version:        %{app_version}
Release:        1%{?dist}
Summary:        Tabbed SSH session manager with folders and multi-session broadcast

License:        MIT
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

# Every runtime dependency ships in the stock Rocky Linux 9/10 AppStream
# repo, so the RPM installs offline with no EPEL or pip.
Requires:       python3
Requires:       python3-gobject
Requires:       gtk3
Requires:       vte291
Requires:       openssh-clients

%description
MultiSSH Manager is a native GTK tabbed SSH session manager for Rocky
Linux: a searchable folder tree of saved hosts, each connection in its own
tab running the system ssh client, per-tab broadcast with live keystroke
mirroring, reconnect-in-place, and import from MultiPuTTY / PuTTY CM XML
exports or ~/.ssh/config.

%prep
%setup -q

%build
# Pure Python application: nothing to compile.

%install
install -d %{buildroot}%{_datadir}/%{name}
cp -a multissh_manager run.py %{buildroot}%{_datadir}/%{name}/

install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/%{name} <<'EOF'
#!/bin/sh
exec python3 %{_datadir}/%{name}/run.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}

install -Dm0644 %{name}.desktop %{buildroot}%{_datadir}/applications/%{name}.desktop
install -Dm0644 multissh_manager/resources/logo.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
install -Dm0644 about.conf.system %{buildroot}%{_sysconfdir}/%{name}/about.conf

%pre
# A 1.x install.sh run left an unowned about.conf; move it aside.
if [ -f %{_sysconfdir}/%{name}/about.conf ] && \
   grep -q "^version=1\." %{_sysconfdir}/%{name}/about.conf 2>/dev/null; then
    mv %{_sysconfdir}/%{name}/about.conf %{_sysconfdir}/%{name}/about.conf.v1-backup || :
fi

%files
%{_datadir}/%{name}/
%{_bindir}/%{name}
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
%dir %{_sysconfdir}/%{name}
%config(noreplace) %{_sysconfdir}/%{name}/about.conf
%doc README.md HELP.md

%post
# rpm decides before %pre runs, so a file moved aside there leaves the new
# copy as .rpmnew; put it in place.
if [ ! -e %{_sysconfdir}/%{name}/about.conf ] && [ -e %{_sysconfdir}/%{name}/about.conf.rpmnew ]; then
    mv %{_sysconfdir}/%{name}/about.conf.rpmnew %{_sysconfdir}/%{name}/about.conf || :
fi
gtk-update-icon-cache -q %{_datadir}/icons/hicolor &>/dev/null || :

%postun
gtk-update-icon-cache -q %{_datadir}/icons/hicolor &>/dev/null || :

%changelog
* Sat Nov 14 2026 Mike <18270693+mike0615@users.noreply.github.com> - 2.0.0-1
- Rewrite: per-tab broadcast targets and live keystroke mirroring,
  reconnect-in-place, sidebar search, drag-and-drop, auto-save,
  ~/.ssh/config import, jump hosts, preferences, neutral branding.
- Shortcuts moved to Ctrl+Shift so shell keys (Ctrl+W/O/S) work.
- Post-login commands wait for a shell prompt.
- Installs under /usr/share (noarch) instead of /usr/lib64.

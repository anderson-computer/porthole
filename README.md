# MultiSSH Manager

A native Linux, tabbed SSH session manager for Rocky Linux 9 and 10, in the
spirit of Multi PuTTY Manager on Windows: a folder tree of saved hosts on the
left, real terminal sessions as tabs on the right, and broadcast input to
many sessions at once.

Every tab runs your system's own `ssh` inside a VTE terminal (the engine
GNOME Terminal uses), so `~/.ssh/config`, `ssh-agent`, keys, `ProxyJump`
and known_hosts all behave exactly as they do in a normal shell.

## Features

- **Folder tree** with live search, drag-and-drop, duplicate, and sort.
  Selecting a folder and pressing Connect opens every host in it.
- **Per-tab broadcast targets.** Each tab has a checkbox. Send a command line
  to every ticked tab, or turn on **Live typing** to mirror every keystroke
  (works with interactive programs like `top`, `vi`, or `sudo` prompts).
- **Reconnect in place.** When a session drops, the tab stays open with the
  error and exit status visible. Press Enter or Ctrl+Shift+R to reconnect.
- **Post-login commands** wait until a shell prompt appears, so they are
  never typed into a host-key, password, or MFA prompt.
- **Jump hosts** (`ssh -J`), per-host key / agent / password-only auth, and
  extra ssh args with proper shell quoting. The host dialog shows the exact
  command it will run.
- **Import** MultiPuTTY / PuTTY Connection Manager / MTPuTTY XML (`.xml` or
  `.dat`) and `~/.ssh/config` (including `Include` files).
- **Auto-save** with atomic writes, a `.bak` copy of the previous version,
  and `0600` permissions.
- **Terminal-friendly shortcuts.** App shortcuts use Ctrl+Shift, so Ctrl+W,
  Ctrl+O, and Ctrl+S still reach the shell (unlike 1.x).
- Preferences for font, scrollback, theme (built-in dark or system), and
  reopening last session's tabs.

## Install (Rocky Linux 9 / 10, works offline)

All runtime dependencies (`python3`, `python3-gobject`, `gtk3`, `vte291`,
`openssh-clients`) are in the stock AppStream repo. You don't need EPEL or pip.

### RPM (recommended)

```bash
sudo dnf install -y rpm-build
./packaging/build-rpm.sh                 # runs the tests, writes dist/*.rpm
sudo dnf install ./dist/multissh-manager-2.0.0-1*.rpm
```

### Script

```bash
sudo ./install.sh                        # installs to /opt/multissh-manager
sudo ./install.sh --uninstall
```

### From source

```bash
sudo dnf install -y python3-gobject gtk3 vte291 openssh-clients
python3 run.py [SESSION_FILE]
```

### Upgrading from 1.x

Your `~/.config/multissh-manager/sessions.json` loads unchanged. If 1.x was
installed with `install.sh`, remove it first (`sudo rm -rf
/opt/multissh-manager /usr/local/bin/multissh-manager /etc/multissh-manager`).

Shortcuts changed: Open, Save, and Close Tab are now Ctrl+Shift+O / S / W.

## Files

| Path | Purpose |
|---|---|
| `~/.config/multissh-manager/sessions.json` | Your session tree (auto-saved) |
| `~/.config/multissh-manager/settings.json` | Preferences and window state |

## Source layout

```
run.py                      entry point (python3 run.py [file])
multissh_manager/
  app.py                    main window: tree, tabs, broadcast, menus, actions
  terminal_tab.py           one session tab: VTE, state, reconnect, post-login
  dialogs.py                host / folder / preferences dialogs
  models.py                 Host / Folder / SessionStore, ssh argv, atomic save
  importer.py               MultiPuTTY-family XML importer
  ssh_config.py             ~/.ssh/config importer
  settings.py               user preferences
  about_config.py           About tab content
  help_content.py           in-app help text (mirrored in HELP.md)
  theme.py, resources/      dark GTK stylesheet and icon
packaging/                  RPM spec and build script
tests/                      unit tests (python3 -m unittest discover -s tests)
```

See [HELP.md](HELP.md) for the full usage guide.

## License

MIT. See [LICENSE](LICENSE).

Author: Mike Anderson — https://www.andersoncomputer.net

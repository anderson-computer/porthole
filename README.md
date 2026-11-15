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
- **Saved passwords** in your login keychain (GNOME Keyring / Secret
  Service) or an AES-256 encrypted vault file with a master password, which
  works offline and on machines without a keyring. ssh gets the password
  through `SSH_ASKPASS` and a private socket, so it is never typed into the
  terminal, written to the session file, or logged.
- **Edit ▸ Options**: choose where the session data file lives, turn on an
  append-only **activity log** (secrets filtered out, never records terminal
  keystrokes or output), plus font, scrollback, theme, and tab restore. All
  options persist across restarts.

## Install (Rocky Linux 9 / 10, works offline)

All runtime dependencies (`python3`, `python3-gobject`, `gtk3`, `vte291`,
`openssh-clients`, `python3-cryptography`, `libsecret`) are in the stock
BaseOS/AppStream repos. You don't need EPEL or pip.

### RPM (recommended)

Download the RPM for your release from the
[Releases](https://github.com/mike0615/multissh-manager-v2/releases) page and
install it with dnf, which pulls the dependencies from the stock repos:

```bash
sudo dnf install ./multissh-manager-2.2.0-1.el9.noarch.rpm    # Rocky 9
sudo dnf install ./multissh-manager-2.2.0-1.el10.noarch.rpm   # Rocky 10
```

To build the RPMs yourself:

```bash
sudo dnf install -y rpm-build desktop-file-utils python3-cryptography python3-gobject-base
./packaging/build-rpm.sh          # native: writes out/*.el9 or out/*.el10 RPM + SRPM
./packaging/build-rpm.sh 9 10     # or both, in Rocky containers (podman/docker)
```

### Script

```bash
sudo ./install.sh                        # installs to /opt/multissh-manager
sudo ./install.sh --uninstall
```

### From source

```bash
sudo dnf install -y python3-gobject gtk3 vte291 openssh-clients python3-cryptography libsecret
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
| `~/.config/multissh-manager/sessions.json` | Your session tree (auto-saved; location changeable in Options) |
| `~/.config/multissh-manager/settings.json` | Options and window state |
| `~/.local/state/multissh-manager/activity.log` | Activity log, when enabled (location changeable) |
| `~/.config/multissh-manager/vault.json` | Encrypted password vault, when used (location changeable) |

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
  settings.py               options (settings.json)
  activity_log.py           optional activity log + secret redaction
  secret_store.py           keychain (libsecret) and encrypted vault backends
  askpass.py                SSH_ASKPASS helper run by ssh
  askpass_server.py         private socket that answers the helper
  about_config.py           About tab content
  help_content.py           in-app help text (mirrored in HELP.md)
  theme.py, resources/      dark GTK stylesheet and icon
packaging/                  RPM spec and build scripts (build-rpm.sh, build-in-container.sh)
tests/                      unit tests (python3 -m unittest discover -s tests)
```

See [HELP.md](HELP.md) for the full usage guide.

## License

MIT. See [LICENSE](LICENSE).

Author: Mike Anderson — https://www.andersoncomputer.net

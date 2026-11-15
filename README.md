# Porthole

> **Formerly MultiSSH Manager.** Version 3.0.0 renamed the app, package and
> command to `porthole`. Upgrading with dnf replaces `multissh-manager`, keeps
> a `multissh-manager` command as a symlink, and copies your old settings,
> sessions and vault on first run (see [Upgrading](#upgrading-from-multissh-manager-2x)).

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
  Ctrl+O, and Ctrl+S still reach the shell.
- **Saved passwords** in your login keychain (GNOME Keyring / Secret
  Service) or an AES-256 encrypted vault file with a master password, which
  works offline and on machines without a keyring. ssh gets the password
  through `SSH_ASKPASS` and a private socket, so it is never typed into the
  terminal, written to the session file, or logged.
- **Open in Skiff.** Right-click a host to open it in the
  [Skiff](https://github.com/mike0615/skiff) SFTP client, already connected
  with the saved user, port, key and password. The password goes through a
  private pipe (`--password-fd`), never argv, the environment or a file.
  The Skiff location is set in Edit ▸ Options ▸ Applications.
- **Edit ▸ Options**: choose where the session data file lives, turn on an
  append-only **activity log** (secrets filtered out, never records terminal
  keystrokes or output), plus font, scrollback, theme, and tab restore. All
  options persist across restarts.

## Install (Rocky Linux 9 / 10, works offline)

All runtime dependencies (`python3`, `python3-gobject`, `gtk3`, `vte291`,
`openssh-clients`, `python3-cryptography`, `libsecret`) are in the stock
BaseOS/AppStream repos. You don't need EPEL or pip.

### RPM (recommended)

Install straight from the
[v3.1.0 release](https://github.com/mike0615/porthole/releases/tag/v3.1.0);
dnf pulls the dependencies from the stock repos:

```bash
# Rocky 9
sudo dnf install https://github.com/mike0615/porthole/releases/download/v3.1.0/porthole-3.1.0-1.el9.noarch.rpm
# Rocky 10
sudo dnf install https://github.com/mike0615/porthole/releases/download/v3.1.0/porthole-3.1.0-1.el10.noarch.rpm
```

### Command line

```bash
porthole                                  # your saved sessions
porthole --connect admin@10.0.0.5:2222    # open a tab and connect now
porthole --host 10.0.0.5 --user admin --port 2222
porthole --connect 10.0.0.5               # asks for the username
```

Anything missing (username, password) is asked for. `porthole --help`
lists every option.

To build the RPMs yourself:

```bash
sudo dnf install -y rpm-build desktop-file-utils python3-cryptography python3-gobject-base
./packaging/build-rpm.sh          # native: writes out/*.el9 or out/*.el10 RPM + SRPM
./packaging/build-rpm.sh 9 10     # or both, in Rocky containers (podman/docker)
```

### Script

```bash
sudo ./install.sh                        # installs to /opt/porthole
sudo ./install.sh --uninstall
```

### From source

```bash
sudo dnf install -y python3-gobject gtk3 vte291 openssh-clients python3-cryptography libsecret
python3 run.py [SESSION_FILE]
```

### Upgrading from MultiSSH Manager 2.x

`sudo dnf install <porthole RPM URL above>` replaces the `multissh-manager`
package (`Obsoletes: multissh-manager < 3.0.0`). `/usr/bin/multissh-manager`
stays as a symlink to `porthole`, so scripts and Sonar keep working.

The first time Porthole starts, and only if `~/.config/porthole` doesn't
exist yet, it **copies** `~/.config/multissh-manager` (settings, sessions,
vault, backups) to `~/.config/porthole` and `~/.local/state/multissh-manager`
to `~/.local/state/porthole`. Options that pointed inside the old folders are
re-pointed at the copies. The originals are never changed or removed.
Passwords saved in the keychain by MultiSSH Manager are found automatically
and copied to Porthole's own keychain entry the first time they're used.

If MultiSSH Manager 1.x was installed with `install.sh`, remove it first
(`sudo rm -rf /opt/multissh-manager /usr/local/bin/multissh-manager /etc/multissh-manager`).

## Files

| Path | Purpose |
|---|---|
| `~/.config/porthole/sessions.json` | Your session tree (auto-saved; location changeable in Options) |
| `~/.config/porthole/settings.json` | Options and window state |
| `~/.local/state/porthole/activity.log` | Activity log, when enabled (location changeable) |
| `~/.config/porthole/vault.json` | Encrypted password vault, when used (location changeable) |

## Source layout

```
run.py                      entry point (python3 run.py [file])
porthole/
  cli.py                    command-line options (--connect, --host, ...)
  migrate.py                first-run copy of MultiSSH Manager 2.x data
  skiff_launch.py           Open in Skiff (argv + pipe password handoff)
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

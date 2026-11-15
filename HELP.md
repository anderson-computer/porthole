# Porthole — Help

_This file mirrors the in-app Help tab (F1)._

## Overview

Porthole keeps a folder tree of saved hosts on the left and opens one real terminal per connection as a tab on the right. Every tab runs your system's own ssh client, so ~/.ssh/config, ssh-agent, keys and ProxyJump all work exactly as they do in a normal terminal.

Quick start
  1. Add hosts with Ctrl+Shift+N, or File ▸ Import a MultiPuTTY / PuTTY CM XML file or your ~/.ssh/config.
  2. Double-click a host to connect. Select several hosts, or a folder, and press Connect to open them all.
  3. Tick the checkbox on each tab you want to control together, then type in the broadcast bar (Enter sends), or turn on Live typing to mirror every keystroke.
  4. Changes save automatically to ~/.config/porthole/sessions.json.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+Shift+N | Add host |
| Ctrl+Shift+F | Filter hosts in the sidebar (Enter connects the first match) |
| Ctrl+Shift+O / S | Open / save session file |
| Ctrl+PageDown / PageUp | Next / previous tab |
| Alt+1 … Alt+9 | Jump to tab 1–9 |
| Ctrl+Shift+W | Close tab |
| Ctrl+Shift+R | Reconnect tab |
| Ctrl+Shift+D | Duplicate tab (new session to the same host) |
| Ctrl+Shift+C / V | Copy / paste in the terminal |
| Ctrl+Shift+B | Toggle broadcast on the current tab |
| Ctrl+Shift+L | Live keystroke mirroring on/off |
| Ctrl+Shift+Enter | Focus the broadcast command bar |
| Ctrl+ + / − / 0 | Zoom terminal in / out / reset |
| Delete / F2 | Delete / edit selected tree item |
| F1 | This help |
| Ctrl+Shift+Q | Quit |

## Adding and editing hosts

Edit ▸ Add Host (Ctrl+Shift+N) opens a form for name, host, port, user, protocol and authentication. The bottom of the form shows the exact command that will run. Authentication:
  • Default — whatever ssh would normally do (config, agent, keys, then password).
  • Specific private key — passes -i <key> -o IdentitiesOnly=yes.
  • SSH agent only — same as Default; use your agent's keys.
  • Password only — disables public-key auth for this host.
Jump host is passed to ssh -J. Extra ssh args respect shell quoting, e.g. -o "ProxyCommand=nc -X 5 -x proxy:1080 %h %p".

Username is the login name on the remote host; the command connects to user@host. You can also type user@host or host:port into Host / IP. If Username is blank you're asked for it when you connect (prefilled with your local login), unless ~/.ssh/config sets a User for that host. Tick "Save this username" to keep it on the host.

## Command line

```
porthole [SESSION_FILE] [--connect [USER@]HOST[:PORT]] [--host HOST] [--user USER]
                 [--port PORT] [--auth default|key|agent|password] [-i KEY] [--name NAME]
```

  • `porthole --connect admin@10.0.0.5:2222`
  • `porthole --host 10.0.0.5 --user admin --port 2222`
  • `porthole --connect 10.0.0.5` (asks for the username)

--connect opens a tab and connects straight away; it can be repeated. Anything missing is asked for: the username in a dialog, the password by ssh in the terminal (or from the keychain/vault for a saved host). Without --auth or -i, a saved host with the same address, port and user is reused with its saved password and options; otherwise the session isn't added to your tree. IPv6: `--connect admin@[fe80::1]:22`.

## Open in Skiff (file transfer)

Right-click a host and choose Open in Skiff to browse its files in the Skiff SFTP client (Skiff 1.3.0 or later). Skiff opens already connected with the host's saved user name and port, its private key ("Specific private key" hosts) and its saved password. If no user name is saved you're asked first; anything else missing, Skiff asks for. The password is handed over through a private pipe that only Skiff inherits: it never appears on the command line, in the environment, in a file or in the activity log, and Skiff doesn't save it. A Skiff that can't take --key or --password-fd still opens, and asks for what it couldn't be given. The item is greyed out when Skiff isn't installed; set its location in Edit ▸ Options ▸ Applications (default /usr/bin/skiff). Jump hosts and extra ssh args aren't passed to Skiff.

## Formerly MultiSSH Manager

Porthole was called MultiSSH Manager up to version 2.2.0. On first start, if ~/.config/porthole doesn't exist, Porthole copies ~/.config/multissh-manager (settings, sessions, vault) and ~/.local/state/multissh-manager (activity log) to the new folders and leaves the originals untouched. Keychain passwords saved by MultiSSH Manager are found automatically and copied to Porthole's own entry the first time they're used. The multissh-manager command still works; it's a link to porthole.

## Organizing the tree

Drag hosts and folders to move them; drop onto a folder to put the item inside it, or between rows to reorder. Right-click for Add / Edit / Duplicate / Delete. Edit ▸ Sort Alphabetically sorts the whole tree. Expanded/collapsed folders are remembered while you work.

## Connecting and reconnecting

Double-click a host, or select hosts or folders and press Connect. Selecting a folder connects every host inside it (you're asked first if that's more than 8). When a session ends the tab stays open with the exit status shown so you can read any error; press Enter in the terminal, click Reconnect, or press Ctrl+Shift+R to reconnect in place. The dot on each tab shows its state: amber connecting, green connected, red closed.

## Broadcasting to multiple sessions

Each terminal tab has a checkbox; ticked tabs are broadcast targets (new tabs start ticked). The counter in the broadcast bar shows ticked / connected tabs, and its menu selects all, none, or inverts.

Command bar: type a line and press Enter to send it, followed by Return, to every ticked tab. Up/Down recall earlier commands.

Live typing (Ctrl+Shift+L): every keystroke typed into a ticked tab is also sent to all other ticked tabs, so interactive programs work across hosts. The bar turns amber while live typing is on. Keystrokes in an unticked tab are never mirrored.

## Post-login commands

Lines in a host's Post-login field are sent once the session has printed its banner/MOTD and gone quiet (700 ms by default; change it in Edit ▸ Options). Each line is sent as a separate command.

## Importing sessions

File ▸ Open loads a native .json store, or opens a MultiPuTTY / PuTTY Connection Manager .xml/.dat export directly (you'll be asked to Save As so the original is never overwritten). File ▸ Import adds an XML export as a new folder without replacing anything.

File ▸ Import ~/.ssh/config adds one entry per concrete Host alias; the alias is used as the hostname so ssh still applies every option from your config. Aliases already in the tree are skipped.

MultiPuTTY's XML schema isn't published, so the importer matches attributes by name. If an export doesn't come through, add its attribute names to the tables at the top of importer.py.

## Saving

Every change is written to the current session file immediately (atomically, with the previous version kept as .bak). The default file is ~/.config/porthole/sessions.json. A tree opened from XML or created with File ▸ New isn't tied to a file until you Save As; the title bar shows • while there are unsaved changes and you'll be asked before quitting.

## Options

Edit ▸ Options holds every setting; all of them are saved to ~/.config/porthole/settings.json as soon as you click Apply and are restored the next time the app starts.

Session data file — where your folders and hosts are stored and loaded from at startup (default ~/.config/porthole/sessions.json). Pick a new location to move your sessions there; if a file already exists at that location you can open it or overwrite it with your current sessions. The previous file is left in place.

Activity log — when enabled, one line per event is appended to the log file (default ~/.local/state/porthole/activity.log, readable only by you).

Passwords — where saved passwords are kept (see Saved passwords).

Terminal — font, scrollback length, theme, post-login delay, whether closing a connected tab asks first, and whether to reopen the last session's tabs on startup.

## Saved passwords

Choose where passwords are kept in Edit ▸ Options ▸ Passwords:
  • System keychain — your login keyring (GNOME Keyring / Secret Service). Unlocked when you log in; no extra password.
  • Encrypted vault — a file encrypted with AES-256-GCM using a key derived from a master password (scrypt). Works on any machine, including headless or airgapped ones. You enter the master password once per session; Edit ▸ Lock Password Vault locks it again.

To save a password, type it in the host's Password field with Save ticked, or tick "Save password" when ssh asks for it. Untick Save to forget it. Deleting a host deletes its saved password.

For hosts with a saved password, ssh asks the app instead of the terminal (through SSH_ASKPASS and a private socket readable only by you). The saved password is supplied once; if the server rejects it, or ssh asks anything else (new host key, passphrase, one-time code, jump host password), a dialog appears. Passwords never appear in the terminal, the session file, the command line, or the activity log.

Switching storage in Options offers to move your saved passwords to the new store.

## Activity log

Recorded: app start and quit; sessions connecting, connected, ended (with exit status), reconnected and closed, with the ssh command used; post-login commands sent; broadcast commands and the tabs they went to; live typing switched on or off; hosts and folders added, edited (which fields changed), duplicated, moved or deleted; files opened, saved and imported; option changes.

Never recorded: anything typed directly into a terminal (including everything sent by live typing), terminal output, and secret values. Commands are passed through a filter that replaces passwords, tokens, API keys, sshpass/mysql -p values, credentials in URLs and similar with ***. A broadcast sent while any target tab is at a password, passphrase or one-time-code prompt is logged without its text. Notes, post-login text and extra ssh args are only logged as "changed" when you edit a host.

Filtering is pattern-based, so avoid putting secrets on the command line where you can; type them at the prompt instead.

## Troubleshooting connections

The first line of every tab shows the exact command being run. If a session closes immediately, read the error left in the tab, then try the same command in a normal terminal, adding -v for verbose output. Telnet hosts require the telnet package (dnf install telnet).

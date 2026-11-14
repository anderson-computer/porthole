# MultiSSH Manager — Help

_This file mirrors the in-app Help tab (F1)._

## Overview

MultiSSH Manager keeps a folder tree of saved hosts on the left and opens one real terminal per connection as a tab on the right. Every tab runs your system's own ssh client, so ~/.ssh/config, ssh-agent, keys and ProxyJump all work exactly as they do in a normal terminal.

Quick start
  1. Add hosts with Ctrl+Shift+N, or File ▸ Import a MultiPuTTY / PuTTY CM XML file or your ~/.ssh/config.
  2. Double-click a host to connect. Select several hosts, or a folder, and press Connect to open them all.
  3. Tick the checkbox on each tab you want to control together, then type in the broadcast bar (Enter sends), or turn on Live typing to mirror every keystroke.
  4. Changes save automatically to ~/.config/multissh-manager/sessions.json.

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

## Organizing the tree

Drag hosts and folders to move them; drop onto a folder to put the item inside it, or between rows to reorder. Right-click for Add / Edit / Duplicate / Delete. Edit ▸ Sort Alphabetically sorts the whole tree. Expanded/collapsed folders are remembered while you work.

## Connecting and reconnecting

Double-click a host, or select hosts or folders and press Connect. Selecting a folder connects every host inside it (you're asked first if that's more than 8). When a session ends the tab stays open with the exit status shown so you can read any error; press Enter in the terminal, click Reconnect, or press Ctrl+Shift+R to reconnect in place. The dot on each tab shows its state: amber connecting, green connected, red closed.

## Broadcasting to multiple sessions

Each terminal tab has a checkbox; ticked tabs are broadcast targets (new tabs start ticked). The counter in the broadcast bar shows ticked / connected tabs, and its menu selects all, none, or inverts.

Command bar: type a line and press Enter to send it, followed by Return, to every ticked tab. Up/Down recall earlier commands.

Live typing (Ctrl+Shift+L): every keystroke typed into a ticked tab is also sent to all other ticked tabs, so interactive programs work across hosts. The bar turns amber while live typing is on. Keystrokes in an unticked tab are never mirrored.

## Post-login commands

Lines in a host's Post-login field are sent once the session has printed its banner/MOTD and gone quiet (700 ms by default; change it in Preferences). Each line is sent as a separate command.

## Importing sessions

File ▸ Open loads a native .json store, or opens a MultiPuTTY / PuTTY Connection Manager .xml/.dat export directly (you'll be asked to Save As so the original is never overwritten). File ▸ Import adds an XML export as a new folder without replacing anything.

File ▸ Import ~/.ssh/config adds one entry per concrete Host alias; the alias is used as the hostname so ssh still applies every option from your config. Aliases already in the tree are skipped.

MultiPuTTY's XML schema isn't published, so the importer matches attributes by name. If an export doesn't come through, add its attribute names to the tables at the top of importer.py.

## Saving

Every change is written to the current session file immediately (atomically, with the previous version kept as .bak). The default file is ~/.config/multissh-manager/sessions.json. A tree opened from XML or created with File ▸ New isn't tied to a file until you Save As; the title bar shows • while there are unsaved changes and you'll be asked before quitting.

## Preferences

File ▸ Preferences sets the terminal font, scrollback length, theme (built-in dark or your system GTK theme), post-login delay, whether closing a connected tab asks first, and whether to reopen the last session's tabs on startup. Stored in ~/.config/multissh-manager/settings.json.

## Troubleshooting connections

The first line of every tab shows the exact command being run. If a session closes immediately, read the error left in the tab, then try the same command in a normal terminal, adding -v for verbose output. Telnet hosts require the telnet package (dnf install telnet).


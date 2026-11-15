"""
GTK3 + VTE desktop application: a tabbed, folder-tree SSH session manager.

  * Sidebar tree of folders and hosts (search, drag-and-drop, auto-saved).
  * Each connection is a tab running the system's own ssh client in VTE.
  * Broadcast a command line, or mirror live keystrokes, to the tabs you
    tick; each tab has its own broadcast checkbox.
  * Disconnected tabs stay open with the error visible and can be
    reconnected in place.
  * Shortcuts use Ctrl+Shift so they never steal keys from the shell.
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path
from typing import List, Optional, Set, Tuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import __version__, importer, ssh_config, theme
from . import about_config as about
from .activity_log import activity, redact
from .askpass_server import AskpassServer
from .dialogs import (AskpassDialog, FolderDialog, HostDialog, MasterPasswordDialog,
                      OptionsDialog, UsernameDialog, confirm, error)
from .secret_store import (BACKEND_KEYRING, BACKEND_LABELS, BACKEND_NONE, BACKEND_VAULT,
                           FileVault, KeyringStore, VaultError, default_vault_path)
from .migrate import migrate_legacy
from .help_content import HELP_TOPICS, OVERVIEW, SHORTCUTS
from .models import Folder, Host, SessionStore, local_user, ssh_config_user
from .settings import Settings, default_data_path
from .terminal_tab import TerminalTab

APP_ID = "us.andersoncomputer.Porthole"
APP_TITLE = "Porthole"

COL_ICON, COL_MARKUP, COL_KIND, COL_ID, COL_TIP = range(5)
DND_TARGET = "MSM_TREE_ROW"

ACCELS = {
    "win.open": ["<Primary><Shift>o"],
    "win.save": ["<Primary><Shift>s"],
    "win.add-host": ["<Primary><Shift>n"],
    "win.find": ["<Primary><Shift>f"],
    "win.close-tab": ["<Primary><Shift>w"],
    "win.reconnect": ["<Primary><Shift>r"],
    "win.duplicate-tab": ["<Primary><Shift>d"],
    "win.copy": ["<Primary><Shift>c"],
    "win.paste": ["<Primary><Shift>v"],
    "win.next-tab": ["<Primary>Page_Down"],
    "win.prev-tab": ["<Primary>Page_Up"],
    "win.toggle-broadcast": ["<Primary><Shift>b"],
    "win.live-broadcast": ["<Primary><Shift>l"],
    "win.focus-broadcast": ["<Primary><Shift>Return"],
    "win.zoom-in": ["<Primary>plus", "<Primary>equal"],
    "win.zoom-out": ["<Primary>minus"],
    "win.zoom-reset": ["<Primary>0"],
    "win.help": ["F1"],
    "app.quit": ["<Primary><Shift>q"],
}


_SAFE_HOST_FIELDS = ("name", "hostname", "port", "username", "protocol", "auth_method",
                     "private_key_path", "jump_host")


def describe_host_changes(before: dict, after: dict) -> str:
    """Summarize edited host fields for the activity log. Free-text fields
    that may hold secrets are reported as changed without their values."""
    parts = []
    for key in Host.__dataclass_fields__:
        if key == "id" or before.get(key) == after.get(key):
            continue
        if key in _SAFE_HOST_FIELDS:
            parts.append(f"{key}: {before.get(key)!r} -> {after.get(key)!r}")
        else:
            parts.append(f"{key}: (changed)")
    return "; ".join(parts) or "no changes"


def _options_snapshot(s: Settings) -> dict:
    return {"data_file": str(s.data_path), "log_enabled": s.log_enabled,
            "password_backend": s.password_backend, "vault_file": s.vault_file,
            "log_file": s.log_file, "font": s.font, "scrollback_lines": s.scrollback_lines,
            "theme": s.theme, "post_login_idle_ms": s.post_login_idle_ms,
            "confirm_close_connected": s.confirm_close_connected,
            "restore_tabs": s.restore_tabs}


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: "Application", open_path: Optional[str] = None):
        super().__init__(application=app, title=APP_TITLE)
        self.settings = app.settings
        self.set_default_size(self.settings.window_width, self.settings.window_height)
        try:
            self.set_icon_from_file(str(theme.LOGO_FILE))
        except GLib.Error:
            pass

        self.session_store = SessionStore()
        self.store_path: Optional[Path] = None
        self.dirty = False
        self.collapsed: Set[str] = set()
        self._special_tabs = {}
        self._history: List[str] = []
        self.askpass = AskpassServer()
        self._vault: Optional[FileVault] = None
        self._keyring: Optional[KeyringStore] = None
        self._history_pos = 0

        self._install_actions()
        self._build_ui()
        self.connect("delete-event", self._on_delete_event)

        if open_path:
            self._open_path(open_path)
        else:
            self._load_initial_store()
        if self.settings.restore_tabs:
            for hid in self.settings.last_open_host_ids:
                host = self.session_store.root.find_host(hid)
                if host:
                    if host.needs_username:
                        # Ask once the window is on screen, not during startup.
                        GLib.idle_add(lambda h=host: self._open_terminal_tab(h) and False)
                    else:
                        self._open_terminal_tab(host)
        self._update_status()

    # ================================================================
    # Actions & menus
    # ================================================================
    def _install_actions(self):
        simple = {
            "new-store": self._on_new_store,
            "open": self._on_open,
            "import-xml": self._on_import_xml,
            "import-ssh-config": self._on_import_ssh_config,
            "save": self._on_save,
            "save-as": self._on_save_as,
            "add-folder": self._add_folder,
            "add-host": self._add_host,
            "edit": self._edit_selected,
            "duplicate": self._duplicate_selected,
            "delete": self._delete_selected,
            "connect": self._connect_selected,
            "sort": self._sort_tree,
            "find": lambda: self.search_entry.grab_focus(),
            "close-tab": self._close_current_tab,
            "reconnect": lambda: self._with_tab(lambda t: t.reconnect()),
            "duplicate-tab": lambda: self._with_tab(
                lambda t: self._open_terminal_tab(t.host, user=t.user_override)),
            "copy": self._copy,
            "paste": self._paste,
            "next-tab": lambda: self._cycle_tab(1),
            "prev-tab": lambda: self._cycle_tab(-1),
            "toggle-broadcast": lambda: self._with_tab(
                lambda t: setattr(t, "broadcast", not t.broadcast)),
            "broadcast-all": lambda: self._set_all_broadcast(True),
            "broadcast-none": lambda: self._set_all_broadcast(False),
            "broadcast-invert": lambda: self._set_all_broadcast(None),
            "focus-broadcast": lambda: self.broadcast_entry.grab_focus(),
            "zoom-in": lambda: self._with_tab(lambda t: t.zoom(1.1)),
            "zoom-out": lambda: self._with_tab(lambda t: t.zoom(1 / 1.1)),
            "zoom-reset": lambda: self._with_tab(lambda t: t.zoom(None)),
            "options": self._on_options,
            "lock-vault": self._lock_vault,
            "help": self._open_help_tab,
            "about": self._open_about_tab,
        }
        for name, cb in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda a, p, cb=cb: cb())
            self.add_action(action)

        self.live_action = Gio.SimpleAction.new_stateful(
            "live-broadcast", None, GLib.Variant.new_boolean(False))
        self.live_action.connect("change-state", self._on_live_changed)
        self.live_action.connect(
            "activate", lambda a, p: a.change_state(GLib.Variant.new_boolean(not a.get_state())))
        self.add_action(self.live_action)

        for i in range(1, 10):
            action = Gio.SimpleAction.new(f"tab-{i}", None)
            action.connect("activate", lambda a, p, n=i - 1: self._goto_tab(n))
            self.add_action(action)
            self.get_application().set_accels_for_action(f"win.tab-{i}", [f"<Alt>{i}"])

    @staticmethod
    def build_menubar() -> Gio.Menu:
        def section(*items):
            m = Gio.Menu()
            for label, action in items:
                m.append(label, action)
            return m

        menubar = Gio.Menu()
        file_menu = Gio.Menu()
        file_menu.append_section(None, section(
            ("_New Session Store", "win.new-store"),
            ("_Open…", "win.open")))
        file_menu.append_section(None, section(
            ("Import MultiPuTTY / PuTTY CM XML…", "win.import-xml"),
            ("Import ~/.ssh/config", "win.import-ssh-config")))
        file_menu.append_section(None, section(
            ("_Save", "win.save"), ("Save _As…", "win.save-as")))
        file_menu.append_section(None, section(
            ("_Quit", "app.quit"),))
        menubar.append_submenu("_File", file_menu)

        edit_menu = Gio.Menu()
        edit_menu.append_section(None, section(
            ("Add _Host…", "win.add-host"), ("Add _Folder…", "win.add-folder")))
        edit_menu.append_section(None, section(
            ("_Edit Selected…", "win.edit"), ("D_uplicate Host", "win.duplicate"),
            ("_Delete Selected", "win.delete")))
        edit_menu.append_section(None, section(
            ("Sort _Alphabetically", "win.sort"), ("_Find Host", "win.find")))
        edit_menu.append_section(None, section(
            ("_Copy", "win.copy"), ("_Paste", "win.paste")))
        edit_menu.append_section(None, section(("_Options…", "win.options"),
                                               ("_Lock Password Vault", "win.lock-vault")))
        menubar.append_submenu("_Edit", edit_menu)

        sess = Gio.Menu()
        sess.append_section(None, section(
            ("_Connect Selected", "win.connect"), ("_Reconnect Tab", "win.reconnect"),
            ("D_uplicate Tab", "win.duplicate-tab"), ("Close _Tab", "win.close-tab")))
        sess.append_section(None, section(
            ("Next Tab", "win.next-tab"), ("Previous Tab", "win.prev-tab")))
        sess.append_section(None, section(
            ("Zoom In", "win.zoom-in"), ("Zoom Out", "win.zoom-out"),
            ("Normal Size", "win.zoom-reset")))
        menubar.append_submenu("_Session", sess)

        bc = Gio.Menu()
        bc.append_section(None, section(
            ("_Live Keystroke Mirroring", "win.live-broadcast"),
            ("_Type Broadcast Command", "win.focus-broadcast")))
        bc.append_section(None, section(
            ("Toggle Current Tab", "win.toggle-broadcast"),
            ("Select _All Tabs", "win.broadcast-all"),
            ("Select _No Tabs", "win.broadcast-none"),
            ("_Invert Selection", "win.broadcast-invert")))
        menubar.append_submenu("_Broadcast", bc)

        help_menu = Gio.Menu()
        help_menu.append("_Help", "win.help")
        help_menu.append("_About", "win.about")
        menubar.append_submenu("_Help", help_menu)
        return menubar

    # ================================================================
    # UI construction
    # ================================================================
    def _build_ui(self):
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root_box)
        root_box.pack_start(self._build_toolbar(), False, False, 0)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_position(self.settings.sidebar_width)
        root_box.pack_start(self.paned, True, True, 0)
        self.paned.pack1(self._build_sidebar(), resize=False, shrink=False)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.pack_start(self._build_broadcast_bar(), False, False, 0)
        self.notebook = Gtk.Notebook(scrollable=True, show_border=False)
        self.notebook.popup_enable()
        self.notebook.connect("page-added", lambda *a: self._update_status())
        self.notebook.connect("page-removed", lambda *a: self._update_status())
        self.notebook.connect("switch-page", self._on_switch_page)
        right.pack_start(self.notebook, True, True, 0)
        right.pack_start(self._build_empty_state(), True, True, 0)
        self.paned.pack2(right, resize=True, shrink=False)

        self.statusbar = Gtk.Statusbar()
        self.status_ctx = self.statusbar.get_context_id("main")
        self.summary_label = Gtk.Label()
        self.summary_label.get_style_context().add_class("msm-dim")
        self.statusbar.pack_end(self.summary_label, False, False, 6)
        root_box.pack_start(self.statusbar, False, False, 0)

    def _build_toolbar(self):
        tb = Gtk.Toolbar()
        tb.set_style(Gtk.ToolbarStyle.BOTH_HORIZ)

        def tool(icon, label, action, tip, important=False):
            btn = Gtk.ToolButton(icon_name=icon, label=label)
            btn.set_action_name(action)
            btn.set_tooltip_text(tip)
            btn.set_is_important(important)
            tb.insert(btn, -1)

        tool("document-open-symbolic", "Open", "win.open", "Open a session file (Ctrl+Shift+O)")
        tool("document-save-symbolic", "Save", "win.save", "Save (Ctrl+Shift+S)")
        tb.insert(Gtk.SeparatorToolItem(), -1)
        tool("list-add-symbolic", "Host", "win.add-host", "Add host (Ctrl+Shift+N)", True)
        tool("folder-new-symbolic", "Folder", "win.add-folder", "Add folder", True)
        tool("document-edit-symbolic", "Edit", "win.edit", "Edit selected")
        tb.insert(Gtk.SeparatorToolItem(), -1)
        tool("network-server-symbolic", "Connect", "win.connect",
             "Connect selected hosts or every host in a selected folder", True)
        tool("view-refresh-symbolic", "Reconnect", "win.reconnect", "Reconnect tab (Ctrl+Shift+R)")
        return tb

    def _build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Filter hosts…  (Ctrl+Shift+F)",
                                            margin=6)
        self.search_entry.connect("search-changed", lambda e: self._refresh_tree())
        self.search_entry.connect("activate", lambda e: self._connect_first_match())
        self.search_entry.connect("stop-search", lambda e: e.set_text(""))
        box.pack_start(self.search_entry, False, False, 0)

        self.tree_store = Gtk.TreeStore(str, str, str, str, str)
        tv = Gtk.TreeView(model=self.tree_store, headers_visible=False,
                          enable_search=False, tooltip_column=COL_TIP)
        self.tree_view = tv
        tv.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        col = Gtk.TreeViewColumn()
        icon = Gtk.CellRendererPixbuf()
        text = Gtk.CellRendererText()
        col.pack_start(icon, False)
        col.pack_start(text, True)
        col.add_attribute(icon, "icon-name", COL_ICON)
        col.add_attribute(text, "markup", COL_MARKUP)
        tv.append_column(col)
        tv.connect("row-activated", self._on_row_activated)
        tv.connect("button-press-event", self._on_tree_button_press)
        tv.connect("key-press-event", self._on_tree_key_press)
        tv.connect("row-expanded", lambda t, it, p: self.collapsed.discard(self.tree_store[it][COL_ID]))
        tv.connect("row-collapsed", lambda t, it, p: self.collapsed.add(self.tree_store[it][COL_ID]))

        targets = [Gtk.TargetEntry.new(DND_TARGET, Gtk.TargetFlags.SAME_WIDGET, 0)]
        tv.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, targets, Gdk.DragAction.MOVE)
        tv.enable_model_drag_dest(targets, Gdk.DragAction.MOVE)
        tv.connect("drag-data-get", self._on_drag_data_get)
        tv.connect("drag-data-received", self._on_drag_data_received)

        sw = Gtk.ScrolledWindow()
        sw.add(tv)
        box.pack_start(sw, True, True, 0)

        btn = Gtk.Button(label="Connect", margin=6)
        btn.get_style_context().add_class("suggested-action")
        btn.set_action_name("win.connect")
        box.pack_start(btn, False, False, 0)
        return box

    def _build_broadcast_bar(self):
        bar = Gtk.Box(spacing=6)
        bar.set_border_width(4)
        bar.get_style_context().add_class("msm-broadcast-bar")
        self.broadcast_bar = bar

        self.broadcast_entry = Gtk.Entry(
            placeholder_text="Command to send to ticked tabs — Enter sends  (Ctrl+Shift+Enter)")
        self.broadcast_entry.set_hexpand(True)
        self.broadcast_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.PRIMARY, "mail-send-symbolic")
        self.broadcast_entry.connect("activate", lambda e: self._send_broadcast())
        self.broadcast_entry.connect("key-press-event", self._on_broadcast_key)
        bar.pack_start(self.broadcast_entry, True, True, 0)

        send = Gtk.Button(label="Send")
        send.connect("clicked", lambda b: self._send_broadcast())
        bar.pack_start(send, False, False, 0)

        live = Gtk.ToggleButton(label="Live typing")
        live.set_action_name("win.live-broadcast")
        live.set_tooltip_text("Mirror every keystroke typed in the active tab to all ticked "
                              "tabs (Ctrl+Shift+L)")
        bar.pack_start(live, False, False, 0)

        targets_menu = Gio.Menu()
        targets_menu.append("All tabs", "win.broadcast-all")
        targets_menu.append("No tabs", "win.broadcast-none")
        targets_menu.append("Invert", "win.broadcast-invert")
        mb = Gtk.MenuButton(menu_model=targets_menu)
        self.targets_label = Gtk.Label(label="0 / 0")
        mb_box = Gtk.Box(spacing=4)
        mb_box.pack_start(self.targets_label, False, False, 0)
        mb_box.pack_start(Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU),
                          False, False, 0)
        mb.add(mb_box)
        mb.set_tooltip_text("Broadcast targets: ticked tabs / open tabs")
        bar.pack_start(mb, False, False, 0)
        return bar

    def _build_empty_state(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(theme.LOGO_FILE), 96, 96, True)
            box.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
        except GLib.Error:
            pass
        title = Gtk.Label(label="No sessions open")
        title.get_style_context().add_class("msm-title")
        hint = Gtk.Label(label="Double-click a host, or select hosts or a folder and press "
                               "Connect.\nAdd hosts with Ctrl+Shift+N or import from "
                               "File ▸ Import.", justify=Gtk.Justification.CENTER)
        hint.get_style_context().add_class("msm-dim")
        box.pack_start(title, False, False, 0)
        box.pack_start(hint, False, False, 0)
        box.set_no_show_all(False)
        self.empty_state = box
        return box

    # ================================================================
    # Store loading / persistence
    # ================================================================
    def _load_initial_store(self):
        path = self.settings.data_path
        self.store_path = path
        if path.exists():
            try:
                self.session_store = SessionStore.load(path)
            except Exception as e:  # keep the bad file; don't autosave over it
                self.store_path = None
                error(self, "Could not load your saved sessions",
                      f"{path}\n\n{e}\n\nA backup may exist at {path}.bak")
                activity.log("file", "load-failed", path=path, error=e)
        self._refresh_tree()
        self._update_title()

    def _open_path(self, path: str):
        try:
            if importer.looks_like_multiputty_file(path):
                root = importer.parse_file(path)
                root.id, root.name = "root", "Sessions"
                self.session_store = SessionStore(root=root)
                self.store_path = None
                self.dirty = True
                self._status(f"Opened {Path(path).name} — use Save As to keep a native copy.")
                activity.log("file", "open-xml", path=path, hosts=root.count()[1])
            else:
                self.session_store = SessionStore.load(Path(path))
                if self.session_store.path is None:  # flat/converted list
                    self.store_path = None
                    self.dirty = True
                    self._status(f"Opened {Path(path).name} — use Save As to keep a native copy.")
                    activity.log("file", "open-converted", path=path,
                                 hosts=self.session_store.root.count()[1])
                    self.collapsed.clear()
                    self._refresh_tree()
                    self._update_title()
                    return
                self.store_path = Path(path)
                self.dirty = False
                self._status(f"Opened {path}")
                activity.log("file", "open", path=path,
                             hosts=self.session_store.root.count()[1])
        except Exception as e:
            error(self, f"Could not open {Path(path).name}", str(e))
            activity.log("file", "open-failed", path=path, error=e)
            return
        self.collapsed.clear()
        self._refresh_tree()
        self._update_title()

    def _changed(self):
        """Call after every edit to the tree: refreshes and auto-saves."""
        self._refresh_tree()
        if self.store_path:
            try:
                self.session_store.save(self.store_path)
                self.dirty = False
            except OSError as e:
                self.dirty = True
                self._status(f"Auto-save failed: {e}")
                activity.log("file", "autosave-failed", path=self.store_path, error=e)
        else:
            self.dirty = True
        self._update_title()
        self._update_status()

    def _update_title(self):
        name = self.store_path.name if self.store_path else "Unsaved"
        self.set_title(f"{'• ' if self.dirty else ''}{name} — {APP_TITLE}")

    def _choose_file(self, title, save=False, filters=("json", "xml", "dat")):
        action = Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN
        dialog = Gtk.FileChooserNative.new(title, self, action,
                                           "_Save" if save else "_Open", "_Cancel")
        if save:
            dialog.set_do_overwrite_confirmation(True)
            dialog.set_current_name("sessions.json")
        f = Gtk.FileFilter()
        f.set_name("Session files")
        for ext in filters:
            f.add_pattern(f"*.{ext}")
        dialog.add_filter(f)
        all_f = Gtk.FileFilter()
        all_f.set_name("All files")
        all_f.add_pattern("*")
        dialog.add_filter(all_f)
        result = dialog.get_filename() if dialog.run() == Gtk.ResponseType.ACCEPT else None
        dialog.destroy()
        return result

    def _maybe_save_before(self, what: str) -> bool:
        """Returns False if the user cancelled."""
        if not self.dirty:
            return True
        d = Gtk.MessageDialog(transient_for=self, modal=True,
                              message_type=Gtk.MessageType.WARNING,
                              buttons=Gtk.ButtonsType.NONE,
                              text=f"Save changes before {what}?")
        d.format_secondary_text("This session tree has not been saved to a file yet.")
        d.add_button("_Discard", Gtk.ResponseType.REJECT)
        d.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        d.add_button("_Save", Gtk.ResponseType.ACCEPT).get_style_context().add_class(
            "suggested-action")
        resp = d.run()
        d.destroy()
        if resp == Gtk.ResponseType.ACCEPT:
            return self._on_save()
        return resp == Gtk.ResponseType.REJECT

    def _on_new_store(self):
        if not self._maybe_save_before("starting a new store"):
            return
        self.session_store = SessionStore()
        self.store_path = None
        self.dirty = False
        self._refresh_tree()
        self._update_title()
        self._status("New empty session store.")
        activity.log("file", "new-store")

    def _on_open(self):
        if not self._maybe_save_before("opening another file"):
            return
        path = self._choose_file("Open session file")
        if path:
            self._open_path(path)

    def _on_import_xml(self):
        path = self._choose_file("Import MultiPuTTY / PuTTY CM file", filters=("xml", "dat"))
        if not path:
            return
        try:
            imported = importer.parse_file(path)
        except Exception as e:
            error(self, f"Could not import {Path(path).name}", str(e))
            return
        imported.name = f"Imported: {Path(path).stem}"
        self.session_store.merge(imported)
        self._changed()
        _, n = imported.count()
        self._status(f"Imported {n} host(s) from {Path(path).name}.")
        activity.log("file", "import-xml", path=path, hosts=n)

    def _on_import_ssh_config(self):
        path = Path.home() / ".ssh" / "config"
        if not path.exists():
            error(self, "No ~/.ssh/config found")
            return
        folder = ssh_config.parse_ssh_config(path)
        existing = {h.hostname for h in self.session_store.root.walk_hosts()}
        folder.hosts = [h for h in folder.hosts if h.hostname not in existing]
        if not folder.hosts:
            self._status("Every host in ~/.ssh/config is already in the tree.")
            return
        self.session_store.merge(folder)
        self._changed()
        self._status(f"Imported {len(folder.hosts)} host(s) from ~/.ssh/config.")
        activity.log("file", "import-ssh-config", path=path, hosts=len(folder.hosts))

    def _on_save(self) -> bool:
        if not self.store_path:
            return self._on_save_as()
        try:
            self.session_store.save(self.store_path)
        except OSError as e:
            error(self, "Save failed", str(e))
            activity.log("file", "save-failed", path=self.store_path, error=e)
            return False
        self.dirty = False
        self._update_title()
        self._status(f"Saved {self.store_path}")
        activity.log("file", "save", path=self.store_path)
        return True

    def _on_save_as(self) -> bool:
        path = self._choose_file("Save session file", save=True, filters=("json",))
        if not path:
            return False
        if not path.endswith(".json"):
            path += ".json"
        self.store_path = Path(path)
        return self._on_save()

    # ================================================================
    # Tree
    # ================================================================
    def _refresh_tree(self):
        needle = self.search_entry.get_text().strip()
        selected = {self.tree_store[it][COL_ID] for _, it in self._selected_rows()}
        self.tree_store.clear()
        root = self.session_store.root
        for sub in root.folders:
            self._populate_folder(None, sub, needle)
        for host in root.hosts:
            if not needle or host.matches(needle):
                self._append_host(None, host)

        # restore expansion + selection
        def visit(model, path, it):
            nid = model[it][COL_ID]
            if model[it][COL_KIND] == "folder" and (needle or nid not in self.collapsed):
                self.tree_view.expand_row(path, False)
            if nid in selected:
                self.tree_view.get_selection().select_iter(it)
            return False

        self.tree_store.foreach(visit)
        self._update_status()

    def _populate_folder(self, parent_iter, folder: Folder, needle: str) -> bool:
        """Adds folder if it (or anything under it) matches. Returns True
        when something was added."""
        folder_hit = bool(needle) and needle.lower() in folder.name.lower()
        _, n = folder.count()
        it = self.tree_store.append(parent_iter, [
            "folder-symbolic",
            f"<b>{escape(folder.name)}</b>  <small><span alpha='55%'>{n}</span></small>",
            "folder", folder.id, f"{folder.name} — {n} host(s)"])
        added = False
        for sub in folder.folders:
            added |= self._populate_folder(it, sub, "" if folder_hit else needle)
        for host in folder.hosts:
            if not needle or folder_hit or host.matches(needle):
                self._append_host(it, host)
                added = True
        if needle and not added and not folder_hit:
            self.tree_store.remove(it)
            return False
        return True

    def _append_host(self, parent_iter, host: Host):
        sub = host.target if host.target != host.name else ""
        if host.port and host.port not in (22, 23):
            sub += f":{host.port}"
        markup = escape(host.name)
        if sub:
            markup += f"  <small><span alpha='55%'>{escape(sub)}</span></small>"
        tip = host.command_preview() + (f"\n\n{host.notes}" if host.notes else "")
        icon = "utilities-terminal-symbolic" if host.protocol == "ssh" else "network-wired-symbolic"
        self.tree_store.append(parent_iter, [icon, markup, "host", host.id, tip])

    def _selected_rows(self):
        model, paths = self.tree_view.get_selection().get_selected_rows()
        return [(model, model.get_iter(p)) for p in paths]

    def _selected_nodes(self):
        out = []
        root = self.session_store.root
        for model, it in self._selected_rows():
            kind, nid = model[it][COL_KIND], model[it][COL_ID]
            node = root.find_folder(nid) if kind == "folder" else root.find_host(nid)
            if node is not None:
                out.append((kind, node))
        return out

    def _target_folder(self) -> Folder:
        """Folder new items go into: the selected folder, the selected host's
        folder, or the top level."""
        nodes = self._selected_nodes()
        root = self.session_store.root
        if not nodes:
            return root
        kind, node = nodes[0]
        if kind == "folder":
            return node
        return root.parent_of(node.id) or root

    def _add_folder(self):
        parent = self._target_folder()
        d = FolderDialog(self)
        if d.run() == Gtk.ResponseType.OK:
            parent.folders.append(Folder(name=d.get_name()))
            activity.log("tree", "add-folder", name=d.get_name(), parent=parent.name)
            self.collapsed.discard(parent.id)
            self._changed()
        d.destroy()

    def _add_host(self):
        parent = self._target_folder()
        d = HostDialog(self, password_store_label=self._password_store_label())
        if d.run() == Gtk.ResponseType.OK:
            host = d.get_host()
            parent.hosts.append(host)
            self._apply_password_action(host, d.password_action())
            activity.log("tree", "add-host", name=host.name, target=host.target,
                         port=host.port, protocol=host.protocol, folder=parent.name)
            self.collapsed.discard(parent.id)
            self._changed()
        d.destroy()

    def _edit_selected(self):
        nodes = self._selected_nodes()
        if not nodes:
            return
        kind, node = nodes[0]
        if kind == "folder":
            d = FolderDialog(self, node)
        else:
            d = HostDialog(self, node, password_store_label=self._password_store_label(),
                           has_saved=node.save_password)
        if d.run() == Gtk.ResponseType.OK:
            if kind == "folder":
                old_name = node.name
                node.name = d.get_name()
                if old_name != node.name:
                    activity.log("tree", "rename-folder", old=old_name, new=node.name)
            else:
                before = node.to_dict()
                d.get_host()
                self._apply_password_action(node, d.password_action())
                activity.log("tree", "edit-host", name=node.name,
                             changes=describe_host_changes(before, node.to_dict()))
                for tab in self._tabs():
                    if tab.host is node:
                        tab.name_label.set_text(node.name)
            self._changed()
        d.destroy()

    def _duplicate_selected(self):
        root = self.session_store.root
        added = 0
        for kind, node in self._selected_nodes():
            if kind != "host":
                continue
            parent = root.parent_of(node.id) or root
            copy = node.clone()
            parent.hosts.insert(parent.hosts.index(node) + 1, copy)
            activity.log("tree", "duplicate-host", source=node.name, name=copy.name)
            added += 1
        if added:
            self._changed()

    def _delete_selected(self):
        nodes = self._selected_nodes()
        if not nodes:
            return
        hosts = sum(1 if k == "host" else node.count()[1] for k, node in nodes)
        names = ", ".join(n.name for _, n in nodes[:3]) + ("…" if len(nodes) > 3 else "")
        if not confirm(self, f"Delete {names}?",
                       f"This removes {len(nodes)} item(s) containing {hosts} host(s). "
                       "Open tabs stay open.", "_Delete", destructive=True):
            return
        root = self.session_store.root
        saved = [h for k, n in nodes for h in ([n] if k == "host" else n.walk_hosts())
                 if h.save_password]
        store = self._open_store() if saved else None
        for h in saved:
            try:
                if store is not None:
                    store.delete(h.id)
                activity.log("vault", "password-removed", host=h.name)
            except VaultError:
                pass
        for kind, node in nodes:
            if kind == "folder":
                root.remove_folder(node.id)
                activity.log("tree", "delete-folder", name=node.name, hosts=node.count()[1])
            else:
                root.remove_host(node.id)
                activity.log("tree", "delete-host", name=node.name, target=node.target)
        self._changed()

    def _sort_tree(self):
        self.session_store.root.sort()
        activity.log("tree", "sort")
        self._changed()

    def _on_row_activated(self, tv, path, _col):
        it = self.tree_store.get_iter(path)
        if self.tree_store[it][COL_KIND] == "host":
            host = self.session_store.root.find_host(self.tree_store[it][COL_ID])
            if host:
                self._open_terminal_tab(host)
        elif tv.row_expanded(path):
            tv.collapse_row(path)
        else:
            tv.expand_row(path, False)

    def _on_tree_key_press(self, _tv, event) -> bool:
        if event.keyval == Gdk.KEY_Delete:
            self._delete_selected()
            return True
        if event.keyval == Gdk.KEY_F2:
            self._edit_selected()
            return True
        return False

    def _on_tree_button_press(self, tv, event) -> bool:
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 3:
            return False
        hit = tv.get_path_at_pos(int(event.x), int(event.y))
        sel = tv.get_selection()
        if hit is None:
            sel.unselect_all()
        elif not sel.path_is_selected(hit[0]):
            sel.unselect_all()
            sel.select_path(hit[0])
        has_sel = sel.count_selected_rows() > 0

        menu = Gtk.Menu()

        def add(label, action, sensitive=True):
            mi = Gtk.MenuItem.new_with_mnemonic(label)
            mi.set_action_name(action)
            mi.set_sensitive(sensitive)
            menu.append(mi)

        add("_Connect", "win.connect", has_sel)
        menu.append(Gtk.SeparatorMenuItem())
        add("Add _Host…", "win.add-host")
        add("Add _Folder…", "win.add-folder")
        menu.append(Gtk.SeparatorMenuItem())
        add("_Edit…", "win.edit", has_sel)
        add("D_uplicate", "win.duplicate", has_sel)
        add("_Delete", "win.delete", has_sel)
        menu.attach_to_widget(tv, None)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    # --- drag and drop -------------------------------------------------
    def _on_drag_data_get(self, tv, _ctx, data, _info, _time):
        ids = [self.tree_store[it][COL_ID] for _, it in self._selected_rows()]
        data.set(data.get_target(), 8, "\n".join(ids).encode("utf-8"))

    def _on_drag_data_received(self, tv, ctx, x, y, data, _info, time):
        # Handle the move in the data model ourselves and rebuild the tree,
        # rather than letting GtkTreeStore shuffle rows on its own.
        tv.stop_emission_by_name("drag-data-received")
        raw = data.get_data()
        ids = raw.decode("utf-8").split("\n") if raw else []
        root = self.session_store.root
        dest = tv.get_dest_row_at_pos(x, y)
        dest_folder, index = root, -1
        if dest is not None:
            path, pos = dest
            it = self.tree_store.get_iter(path)
            kind, nid = self.tree_store[it][COL_KIND], self.tree_store[it][COL_ID]
            into = pos in (Gtk.TreeViewDropPosition.INTO_OR_BEFORE,
                           Gtk.TreeViewDropPosition.INTO_OR_AFTER)
            if kind == "folder" and into:
                dest_folder = root.find_folder(nid) or root
            else:
                dest_folder = root.parent_of(nid) or root
                siblings = dest_folder.folders if kind == "folder" else dest_folder.hosts
                idx = next((i for i, n in enumerate(siblings) if n.id == nid), -1)
                if idx >= 0:
                    index = idx + (0 if pos == Gtk.TreeViewDropPosition.BEFORE else 1)
        moved = 0
        for nid in ids:
            if nid and root.move(nid, dest_folder.id, index):
                moved += 1
                node = root.find_folder(nid) or root.find_host(nid)
                activity.log("tree", "move", name=node.name if node else nid,
                             to=dest_folder.name if dest_folder is not root else "(top level)")
                if index >= 0:
                    index += 1
        Gtk.drag_finish(ctx, moved > 0, False, time)
        if moved:
            self.collapsed.discard(dest_folder.id)
            self._changed()

    # ================================================================
    # Tabs
    # ================================================================
    def _tabs(self) -> List[TerminalTab]:
        return [self.notebook.get_nth_page(i) for i in range(self.notebook.get_n_pages())
                if isinstance(self.notebook.get_nth_page(i), TerminalTab)]

    def _current_tab(self) -> Optional[TerminalTab]:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        return page if isinstance(page, TerminalTab) else None

    def _with_tab(self, fn):
        tab = self._current_tab()
        if tab:
            fn(tab)

    def _connect_selected(self):
        hosts: List[Host] = []
        for kind, node in self._selected_nodes():
            if kind == "host":
                hosts.append(node)
            else:
                hosts.extend(node.walk_hosts())
        seen = set()
        hosts = [h for h in hosts if not (h.id in seen or seen.add(h.id))]
        if not hosts:
            self._status("Select one or more hosts (or a folder) to connect.")
            return
        if len(hosts) > 8 and not confirm(self, f"Open {len(hosts)} sessions?", ok_label="_Connect"):
            return
        for h in hosts:
            self._open_terminal_tab(h)

    def _connect_first_match(self):
        for model, it in self._iter_rows():
            if model[it][COL_KIND] == "host":
                host = self.session_store.root.find_host(model[it][COL_ID])
                if host:
                    self._open_terminal_tab(host)
                return

    def _iter_rows(self):
        out = []
        self.tree_store.foreach(lambda m, p, it: out.append((m, it)) and False)
        return out

    def _ask_username(self, host: Host) -> Tuple[bool, Optional[str]]:
        """For an ssh host with no saved username, ask for one. Returns
        (go_ahead, one_off_user). Skipped when ~/.ssh/config already sets a
        User for the host. Ticking "Save" stores it on the host."""
        if not host.needs_username:
            return True, None
        if ssh_config_user(host.hostname, host.port):
            return True, None
        in_tree = self.session_store.root.find_host(host.id) is host
        label = host.name if host.name in (host.hostname, "") else f"{host.name} ({host.hostname})"
        d = UsernameDialog(self, label, local_user(), can_remember=in_tree)
        ok = d.run() == Gtk.ResponseType.OK
        user, remember = d.get_user(), d.remember
        d.destroy()
        if not ok or not user:
            self._status(f"Connection to {host.name} cancelled (no username).")
            return False, None
        if remember:
            before = host.to_dict()
            host.username = user
            activity.log("tree", "edit-host", name=host.name,
                         changes=describe_host_changes(before, host.to_dict()))
            self._changed()
            return True, None
        return True, user

    def _open_terminal_tab(self, host: Host, user: Optional[str] = None,
                           ask: bool = True) -> Optional[TerminalTab]:
        """Open a tab and connect. Returns None if the user cancelled the
        username prompt."""
        if user is None and ask:
            go, user = self._ask_username(host)
            if not go:
                return None
        tab = TerminalTab(host, self.settings)
        tab.user_override = user or None
        tab.env_provider = self._askpass_env
        tab.connect("user-input", self._on_tab_user_input)
        tab.connect("session-state-changed", lambda t: self._update_status())
        tab.connect("close-requested", self._close_tab)
        idx = self.notebook.append_page(tab, tab.tab_label)
        self.notebook.set_tab_reorderable(tab, True)
        self.notebook.set_menu_label_text(tab, host.name)
        self.notebook.set_current_page(idx)
        tab.connect_session()
        tab.terminal.grab_focus()
        self._status(f"Connecting to {host.name} ({host.target_for(tab.user_override)})…")
        return tab

    def connect_adhoc(self, host: Host) -> Optional[TerminalTab]:
        """--connect / --host from the command line. Unless --auth/-i was
        given, reuses a saved host with the same address, port and user (so
        its saved password, key and options apply); otherwise opens a
        one-off session that isn't added to the tree."""
        explicit_auth = host.auth_method != "default" or bool(host.private_key_path)
        for h in ([] if explicit_auth else self.session_store.root.walk_hosts()):
            if (h.protocol == host.protocol and h.hostname == host.hostname
                    and int(h.port) == int(host.port)
                    and (not host.username or h.username in ("", host.username))):
                activity.log("session", "cli-connect", host=h.name, saved=True)
                return self._open_terminal_tab(h, user=host.username or None)
        activity.log("session", "cli-connect", host=host.hostname, saved=False)
        return self._open_terminal_tab(host)

    def _close_tab(self, tab: TerminalTab) -> bool:
        if tab.alive and self.settings.confirm_close_connected:
            if not confirm(self, f"Close {tab.host.name}?",
                           "The session is still connected and will be terminated.",
                           "_Close", destructive=True):
                return False
        activity.log("session", "close-tab", host=tab.host.name, state=tab.state)
        self.askpass.unregister(tab.askpass_token)
        tab.terminate()
        n = self.notebook.page_num(tab)
        if n != -1:
            self.notebook.remove_page(n)
        tab.destroy()
        return True

    def _close_current_tab(self):
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        if isinstance(page, TerminalTab):
            self._close_tab(page)
        elif page is not None:
            self._close_special(page)

    def _cycle_tab(self, step: int):
        n = self.notebook.get_n_pages()
        if n:
            self.notebook.set_current_page((self.notebook.get_current_page() + step) % n)

    def _goto_tab(self, n: int):
        if n < self.notebook.get_n_pages():
            self.notebook.set_current_page(n)

    def _on_switch_page(self, _nb, page, _num):
        if isinstance(page, TerminalTab):
            GLib.idle_add(page.terminal.grab_focus)

    def _copy(self):
        focus = self.get_focus()
        if isinstance(focus, Gtk.Editable):
            focus.copy_clipboard()
        else:
            self._with_tab(lambda t: t.copy())

    def _paste(self):
        focus = self.get_focus()
        if isinstance(focus, Gtk.Editable):
            focus.paste_clipboard()
        else:
            self._with_tab(lambda t: t.paste())

    # ================================================================
    # Broadcast
    # ================================================================
    def _targets(self) -> List[TerminalTab]:
        return [t for t in self._tabs() if t.broadcast and t.alive]

    def _send_broadcast(self):
        text = self.broadcast_entry.get_text()
        targets = self._targets()
        if not targets:
            self._status("No connected tabs are ticked for broadcast.")
            return
        hidden = [t.host.name for t in targets if t.at_secret_prompt()]
        for t in targets:
            t.send_line(text)
        names = ", ".join(t.host.name for t in targets)
        if hidden:
            activity.log("broadcast", "send", tabs=len(targets), hosts=names,
                         command="[not recorded: sent at a password prompt on "
                                 + ", ".join(hidden) + "]")
        else:
            activity.log("broadcast", "send", tabs=len(targets), hosts=names,
                         command=redact(text) if text else "[Enter]")
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._history_pos = len(self._history)
        self.broadcast_entry.set_text("")
        self._status(f"Sent to {len(targets)} tab(s).")

    def _on_broadcast_key(self, entry, event) -> bool:
        if not self._history or event.keyval not in (Gdk.KEY_Up, Gdk.KEY_Down):
            return False
        step = -1 if event.keyval == Gdk.KEY_Up else 1
        self._history_pos = max(0, min(len(self._history), self._history_pos + step))
        entry.set_text(self._history[self._history_pos]
                       if self._history_pos < len(self._history) else "")
        entry.set_position(-1)
        return True

    def _on_live_changed(self, action, value):
        action.set_state(value)
        ctx = self.broadcast_bar.get_style_context()
        if value.get_boolean():
            ctx.add_class("msm-broadcast-live")
            self._status("Live typing ON: keystrokes in the active tab go to every ticked tab.")
            activity.log("broadcast", "live-typing-on",
                         tabs=", ".join(t.host.name for t in self._targets()))
        else:
            ctx.remove_class("msm-broadcast-live")
            self._status("Live typing off.")
            activity.log("broadcast", "live-typing-off")

    def _on_tab_user_input(self, source: TerminalTab, text: str):
        if not self.live_action.get_state().get_boolean() or not source.broadcast:
            return
        for t in self._targets():
            if t is not source:
                t.send_text(text)

    def _set_all_broadcast(self, value: Optional[bool]):
        for t in self._tabs():
            t.broadcast = (not t.broadcast) if value is None else value

    # ================================================================
    # Help / About (non-terminal tabs)
    # ================================================================
    def _special_tab(self, key: str, title: str, build_fn):
        page = self._special_tabs.get(key)
        if page is not None and self.notebook.page_num(page) != -1:
            self.notebook.set_current_page(self.notebook.page_num(page))
            return
        page = build_fn()
        label = Gtk.Box(spacing=4)
        label.pack_start(Gtk.Label(label=title), False, False, 0)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda b: self._close_special(page))
        label.pack_start(close, False, False, 0)
        label.show_all()
        page.show_all()
        idx = self.notebook.append_page(page, label)
        self.notebook.set_current_page(idx)
        self._special_tabs[key] = page

    def _close_special(self, page):
        n = self.notebook.page_num(page)
        if n != -1:
            self.notebook.remove_page(n)
        for k, v in list(self._special_tabs.items()):
            if v is page:
                del self._special_tabs[k]

    def _open_help_tab(self):
        def build():
            sw = Gtk.ScrolledWindow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=20)
            t = Gtk.Label(label=f"{APP_TITLE} — Help", xalign=0)
            t.get_style_context().add_class("msm-title")
            box.pack_start(t, False, False, 0)
            ov = Gtk.Label(label=OVERVIEW, xalign=0, wrap=True, max_width_chars=100)
            box.pack_start(ov, False, False, 0)

            grid = Gtk.Grid(column_spacing=24, row_spacing=4, margin_top=6)
            for i, (keys, desc) in enumerate(SHORTCUTS):
                k = Gtk.Label(xalign=0)
                k.set_markup(f"<tt>{escape(keys)}</tt>")
                k.get_style_context().add_class("msm-accent")
                grid.attach(k, 0, i, 1, 1)
                grid.attach(Gtk.Label(label=desc, xalign=0), 1, i, 1, 1)
            exp = Gtk.Expander(label="Keyboard shortcuts", expanded=True)
            exp.add(grid)
            box.pack_start(exp, False, False, 0)

            topics = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            topics.get_style_context().add_class("msm-help-topics")
            for heading, body in HELP_TOPICS:
                e = Gtk.Expander(label=heading)
                lbl = Gtk.Label(label=body, xalign=0, wrap=True, max_width_chars=100,
                                selectable=True, margin_start=14, margin_top=4, margin_bottom=8)
                e.add(lbl)
                topics.pack_start(e, False, False, 0)
            box.pack_start(topics, False, False, 0)
            sw.add(box)
            return sw

        self._special_tab("help", "Help", build)

    def _open_about_tab(self):
        def build():
            sw = Gtk.ScrolledWindow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=32,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(about.LOGO_FILE), 128, 128, True)
                box.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
            except GLib.Error:
                pass
            name = Gtk.Label(label=about.APP_NAME)
            name.get_style_context().add_class("msm-title")
            box.pack_start(name, False, False, 0)
            ver = Gtk.Label(label=f"Version {about.VERSION}")
            ver.get_style_context().add_class("msm-dim")
            box.pack_start(ver, False, False, 0)
            box.pack_start(Gtk.Label(label=about.TAGLINE), False, False, 0)
            box.pack_start(Gtk.Label(label=f"Author: {about.AUTHOR}"), False, False, 4)
            links = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            for label, url in about.LINKS:
                links.pack_start(Gtk.LinkButton.new_with_label(url, label), False, False, 0)
            box.pack_start(links, False, False, 6)
            lic = Gtk.Label(label=about.LICENSE_NOTICE, wrap=True, max_width_chars=72,
                            justify=Gtk.Justification.CENTER, selectable=True)
            lic.get_style_context().add_class("msm-dim")
            box.pack_start(lic, False, False, 16)
            sw.add(box)
            return sw

        self._special_tab("about", "About", build)

    # ================================================================
    # Options, status, shutdown
    # ================================================================
    def _on_options(self):
        d = OptionsDialog(self, self.settings)
        d.change_master_btn.connect("clicked", lambda b: self._change_master(d.vault_path()))
        while True:
            if d.run() != Gtk.ResponseType.OK:
                d.destroy()
                return
            new_data = d.data_path()
            if new_data.is_dir():
                error(self, "Choose a file, not a folder", str(new_data))
                continue
            break
        before = _options_snapshot(self.settings)
        old_data = self.settings.data_path
        old_backend, old_vault = self.settings.password_backend, self._vault_path()
        new_backend = d.backend.get_active_id() or BACKEND_NONE
        d.apply_to(self.settings)
        d.destroy()

        # Turn logging off/on/move with a record in the right file.
        log_changed = (before["log_enabled"], before["log_file"]) != \
            (self.settings.log_enabled, self.settings.log_file)
        if log_changed:
            if activity.enabled:
                activity.log("options", "activity-log-off" if not self.settings.log_enabled
                             else "activity-log-moved", to=self.settings.log_file or "default")
            err = activity.configure(self.settings.log_enabled, self.settings.log_file)
            if err:
                self.settings.log_enabled = False
                error(self, "Activity log not enabled", err)
            elif self.settings.log_enabled:
                activity.log("options", "activity-log-on", path=activity.path,
                             version=__version__)

        if new_data != old_data and self._change_data_file(old_data, new_data):
            self.settings.data_file = "" if new_data == default_data_path() else str(new_data)

        if self._vault_path() != old_vault and self._vault is not None:
            self._vault.lock()
            self._vault = None
        if new_backend != old_backend:
            self._switch_backend(old_backend, new_backend, old_vault)

        after = _options_snapshot(self.settings)
        changed = {k: v for k, v in after.items()
                   if before.get(k) != v and not k.startswith("log_")}
        if changed:
            activity.log("options", "changed", **changed)

        err = self.settings.save()
        if err:
            error(self, "Options were applied but not saved", err)
        else:
            self._status("Options saved.")
        theme.apply_theme(self.settings.theme)
        for t in self._tabs():
            t.apply_settings(self.settings)

    def _change_data_file(self, old: Path, new: Path) -> bool:
        """Point the default session file at `new`. Returns False if the
        user cancelled or it failed (the old location stays in effect)."""
        if new.exists():
            dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                    message_type=Gtk.MessageType.QUESTION,
                                    buttons=Gtk.ButtonsType.NONE,
                                    text="A session file already exists there")
            dlg.format_secondary_text(
                f"{new}\n\nOpen the sessions in that file, or overwrite it with the "
                "sessions you have now? (The old file is kept as a .bak copy.)")
            dlg.add_button("_Cancel", Gtk.ResponseType.CANCEL)
            dlg.add_button("_Overwrite", 2).get_style_context().add_class("destructive-action")
            dlg.add_button("Open _It", 1).get_style_context().add_class("suggested-action")
            resp = dlg.run()
            dlg.destroy()
            if resp == 1:
                try:
                    store = SessionStore.load(new)
                except Exception as e:
                    error(self, f"Could not open {new.name}", str(e))
                    return False
                self.session_store = store
                self.collapsed.clear()
                activity.log("file", "data-file-changed", old=old, new=new, action="opened")
            elif resp != 2:
                return False
            else:
                activity.log("file", "data-file-changed", old=old, new=new,
                             action="overwritten")
        else:
            activity.log("file", "data-file-changed", old=old, new=new, action="created")
        self.store_path = new
        try:
            self.session_store.save(new)
        except OSError as e:
            error(self, "Could not write the session file", f"{new}\n\n{e}")
            self.store_path = old if old.exists() else None
            return False
        self.dirty = False
        self._refresh_tree()
        self._update_title()
        return True

    # ================================================================
    # Saved passwords (keychain / vault) and askpass
    # ================================================================
    def _vault_path(self) -> Path:
        v = self.settings.vault_file
        return Path(v).expanduser() if v else default_vault_path()

    def _password_store_label(self) -> Optional[str]:
        b = self.settings.password_backend
        if b == BACKEND_KEYRING:
            return "system keychain"
        if b == BACKEND_VAULT:
            return "encrypted vault"
        return None

    def _open_store(self, backend: Optional[str] = None, create: bool = False,
                    vault_path: Optional[Path] = None):
        """Return a ready-to-use store, prompting to unlock/create the vault
        when needed. None if unavailable or the user cancelled."""
        backend = backend or self.settings.password_backend
        if backend == BACKEND_KEYRING:
            if self._keyring is None:
                self._keyring = KeyringStore()
            if not self._keyring.available:
                error(self, "System keychain not available",
                      self._keyring.error or "No Secret Service (GNOME Keyring/KWallet) "
                      "is running in this session. Use the encrypted vault instead.")
                return None
            return self._keyring
        if backend != BACKEND_VAULT:
            return None
        path = vault_path or self._vault_path()
        vault = self._vault if self._vault is not None and self._vault.path == path \
            else FileVault(path)
        if vault.unlocked:
            self._vault = vault
            return vault
        message = ""
        while True:
            if not vault.exists and not create:
                return None
            mode = "unlock" if vault.exists else "create"
            dlg = MasterPasswordDialog(self, mode, path, message)
            resp = dlg.run()
            current, new = dlg.current_password(), dlg.new_password()
            dlg.destroy()
            if resp != Gtk.ResponseType.OK:
                return None
            try:
                if mode == "create":
                    vault.create(new)
                    activity.log("vault", "created", path=path)
                else:
                    vault.unlock(current)
                    activity.log("vault", "unlocked", path=path)
            except VaultError as e:
                activity.log("vault", f"{mode}-failed", path=path, error=e)
                message = str(e)
                continue
            self._vault = vault
            return vault

    def _lock_vault(self):
        if self._vault is not None and self._vault.unlocked:
            self._vault.lock()
            activity.log("vault", "locked")
            self._status("Password vault locked.")
        else:
            self._status("The password vault is not unlocked.")

    def _change_master(self, path: Path):
        vault = FileVault(path)
        if not vault.exists:
            error(self, "No vault yet", "A vault is created the first time you save a password.")
            return
        message = ""
        while True:
            dlg = MasterPasswordDialog(self, "change", path, message)
            resp = dlg.run()
            old, new = dlg.current_password(), dlg.new_password()
            dlg.destroy()
            if resp != Gtk.ResponseType.OK:
                return
            try:
                vault.change_master(old, new)
            except VaultError as e:
                message = str(e)
                continue
            if self._vault is not None and self._vault.path == path:
                self._vault = vault
            activity.log("vault", "master-password-changed", path=path)
            self._status("Master password changed.")
            return

    def _get_saved_password(self, host: Host) -> Optional[str]:
        store = self._open_store()
        if store is None:
            return None
        try:
            return store.get(host.id)
        except VaultError as e:
            self._status(str(e))
            return None

    def _save_password(self, host: Host, password: str) -> bool:
        store = self._open_store(create=True)
        if store is None:
            return False
        try:
            if isinstance(store, KeyringStore):
                store.set(host.id, password, f"Porthole: {host.name} ({host.target})")
            else:
                store.set(host.id, password)
        except VaultError as e:
            error(self, "Could not save the password", str(e))
            return False
        host.save_password = True
        activity.log("vault", "password-saved", host=host.name,
                     store=self.settings.password_backend)
        return True

    def _forget_password(self, host: Host) -> None:
        store = self._open_store()
        if store is not None:
            try:
                store.delete(host.id)
            except VaultError as e:
                error(self, "Could not remove the saved password", str(e))
                return
        host.save_password = False
        activity.log("vault", "password-removed", host=host.name)

    def _apply_password_action(self, host: Host, action) -> None:
        what, value = action
        if what == "set":
            self._save_password(host, value)
        elif what == "forget":
            self._forget_password(host)

    def _switch_backend(self, old: str, new: str, old_vault: Path) -> None:
        hosts = [h for h in self.session_store.root.walk_hosts() if h.save_password]
        if old == BACKEND_NONE or not hosts:
            return
        if new == BACKEND_NONE:
            if confirm(self, f"Delete {len(hosts)} saved password(s)?",
                       f"They are stored in the {BACKEND_LABELS[old].lower()}. "
                       "Cancel keeps them there in case you switch back.",
                       "_Delete", destructive=True):
                src = self._open_store(old, vault_path=old_vault)
                for h in hosts:
                    try:
                        if src:
                            src.delete(h.id)
                    except VaultError:
                        pass
                    h.save_password = False
                activity.log("vault", "passwords-deleted", count=len(hosts), store=old)
                self._changed()
            return
        if not confirm(self, f"Move {len(hosts)} saved password(s)?",
                       f"From the {BACKEND_LABELS[old].lower()} to the "
                       f"{BACKEND_LABELS[new].lower()}.", "_Move"):
            return
        src = self._open_store(old, vault_path=old_vault)
        dst = self._open_store(new, create=True)
        if src is None or dst is None:
            error(self, "Passwords not moved",
                  "Both the old and new password stores must be available.")
            return
        moved = 0
        for h in hosts:
            try:
                pw = src.get(h.id)
                if pw is None:
                    continue
                if isinstance(dst, KeyringStore):
                    dst.set(h.id, pw, f"Porthole: {h.name} ({h.target})")
                else:
                    dst.set(h.id, pw)
                src.delete(h.id)
                moved += 1
            except VaultError as e:
                error(self, f"Could not move the password for {h.name}", str(e))
                break
        activity.log("vault", "passwords-moved", count=moved, source=old, destination=new)
        self._status(f"Moved {moved} saved password(s).")

    def _askpass_env(self, tab: TerminalTab):
        """Environment for ssh so it asks us (not the terminal) for the saved
        password. Only used for ssh hosts marked "save password"."""
        self.askpass.unregister(tab.askpass_token)
        tab.askpass_token = None
        tab.saved_pw_attempts = 0
        host = tab.host
        if (not host.save_password or host.protocol != "ssh"
                or self.settings.password_backend == BACKEND_NONE):
            return None
        try:
            self.askpass.start()
        except OSError as e:
            self._status(f"Saved password unavailable: {e}")
            return None
        tab.askpass_token = self.askpass.register(
            lambda prompt, kind: self._on_askpass(tab, prompt, kind))
        return self.askpass.env_for(tab.askpass_token)

    def _on_askpass(self, tab: TerminalTab, prompt: str, kind: str) -> Optional[str]:
        host = tab.host
        lower = prompt.lower()
        is_password = "password" in lower and kind not in ("confirm", "none")
        jump_names = [p.split("@")[-1].split(":")[0]
                      for p in host.jump_host.split(",") if p.strip()]
        for_jump = any(j and j in prompt for j in jump_names)
        note = ""
        if is_password and not for_jump:
            if tab.saved_pw_attempts == 0:
                tab.saved_pw_attempts += 1
                pw = self._get_saved_password(host)
                if pw is not None:
                    activity.log("session", "password-supplied", host=host.name,
                                 store=self.settings.password_backend)
                    return pw
            else:
                note = "The saved password was not accepted."
        activity.log("session", "askpass-prompt", host=host.name,
                     prompt=prompt.strip().splitlines()[-1][:120] if prompt.strip() else "",
                     kind=kind or "text")
        save_label = self._password_store_label() if is_password and not for_jump else None
        dlg = AskpassDialog(self, host.name, prompt, kind, save_label, note)
        if save_label and dlg.save_check is not None:
            dlg.save_check.set_active(bool(note) or not host.save_password)
        answer = dlg.answer()
        wants_save = dlg.wants_save
        dlg.destroy()
        if answer and wants_save:
            if self._save_password(host, answer):
                self._changed()
        return answer

    def _status(self, text: str):
        self.statusbar.pop(self.status_ctx)
        self.statusbar.push(self.status_ctx, text)

    def _update_status(self):
        if not hasattr(self, "notebook"):
            return
        tabs = self._tabs()
        live = sum(1 for t in tabs if t.alive)
        targets = len(self._targets())
        _, n_hosts = self.session_store.root.count()
        self.targets_label.set_text(f"{targets} / {live}")
        self.summary_label.set_text(f"{n_hosts} hosts  ·  {live} connected  ·  "
                                    f"{targets} broadcast target(s)")
        has_pages = self.notebook.get_n_pages() > 0
        self.notebook.set_visible(has_pages)
        self.empty_state.set_visible(not has_pages)

    def _on_delete_event(self, *_):
        return not self.prepare_quit()

    def prepare_quit(self) -> bool:
        live = [t for t in self._tabs() if t.alive]
        if live and self.settings.confirm_close_connected:
            if not confirm(self, f"Quit with {len(live)} connected session(s)?",
                           "All sessions will be terminated.", "_Quit", destructive=True):
                return False
        if not self._maybe_save_before("quitting"):
            return False
        w, h = self.get_size()
        self.settings.window_width, self.settings.window_height = w, h
        self.settings.sidebar_width = self.paned.get_position()
        self.settings.last_open_host_ids = [t.host.id for t in self._tabs()]
        self.settings.save()
        activity.log("app", "quit", open_tabs=len(self._tabs()))
        if self._vault is not None:
            self._vault.lock()
        self.askpass.stop()
        for t in self._tabs():
            t.terminate()
        return True


class Application(Gtk.Application):
    def __init__(self, open_path: Optional[str] = None, connect_hosts: Optional[List[Host]] = None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.open_path = open_path
        self.connect_hosts = list(connect_hosts or [])
        # Copy MultiSSH Manager 2.x data on first run (before loading settings).
        self.migration_note = migrate_legacy()
        self.settings = Settings.load()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name(APP_TITLE)
        theme.apply_theme(self.settings.theme)
        self._log_error = activity.configure(self.settings.log_enabled, self.settings.log_file)
        activity.log("app", "start", version=__version__, data_file=self.settings.data_path)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit)
        self.add_action(quit_action)
        for action, accels in ACCELS.items():
            self.set_accels_for_action(action, accels)
        self.set_menubar(MainWindow.build_menubar())

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(self, self.open_path)
            if self._log_error:
                win._status(self._log_error)
            elif self.migration_note:
                win._status(self.migration_note)
            if self.migration_note:
                activity.log("app", "migrated", detail=self.migration_note)
        win.show_all()
        win._update_status()
        win.present()
        hosts, self.connect_hosts = self.connect_hosts, []
        for h in hosts:
            GLib.idle_add(lambda h=h: win.connect_adhoc(h) and False)

    def _on_quit(self, *_):
        win = self.props.active_window
        if win is None or win.prepare_quit():
            self.quit()


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    from .cli import build_parser, hosts_from_args
    parser = build_parser(f"{APP_TITLE} {__version__}")
    args = parser.parse_args(argv[1:])
    try:
        hosts = hosts_from_args(args)
    except ValueError as e:
        parser.error(str(e))
    return Application(args.session_file, hosts).run(argv[:1])


if __name__ == "__main__":
    sys.exit(main())

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
from typing import List, Optional, Set

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import __version__, importer, ssh_config, theme
from .about_config import LICENSE_NOTICE, load_about_info
from .dialogs import FolderDialog, HostDialog, PreferencesDialog, confirm, error
from .help_content import HELP_TOPICS, OVERVIEW, SHORTCUTS
from .models import Folder, Host, SessionStore
from .settings import Settings
from .terminal_tab import TerminalTab

APP_ID = "us.andersoncomputer.MultiSSHManager"
APP_TITLE = "MultiSSH Manager"

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
            "duplicate-tab": lambda: self._with_tab(lambda t: self._open_terminal_tab(t.host)),
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
            "preferences": self._on_preferences,
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
            ("_Preferences", "win.preferences"), ("_Quit", "app.quit")))
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
        path = SessionStore.default_path()
        self.store_path = path
        if path.exists():
            try:
                self.session_store = SessionStore.load(path)
            except Exception as e:  # keep the bad file; don't autosave over it
                self.store_path = None
                error(self, "Could not load your saved sessions",
                      f"{path}\n\n{e}\n\nA backup may exist at {path}.bak")
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
            else:
                self.session_store = SessionStore.load(Path(path))
                self.store_path = Path(path)
                self.dirty = False
                self._status(f"Opened {path}")
        except Exception as e:
            error(self, f"Could not open {Path(path).name}", str(e))
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

    def _on_save(self) -> bool:
        if not self.store_path:
            return self._on_save_as()
        try:
            self.session_store.save(self.store_path)
        except OSError as e:
            error(self, "Save failed", str(e))
            return False
        self.dirty = False
        self._update_title()
        self._status(f"Saved {self.store_path}")
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
            self.collapsed.discard(parent.id)
            self._changed()
        d.destroy()

    def _add_host(self):
        parent = self._target_folder()
        d = HostDialog(self)
        if d.run() == Gtk.ResponseType.OK:
            parent.hosts.append(d.get_host())
            self.collapsed.discard(parent.id)
            self._changed()
        d.destroy()

    def _edit_selected(self):
        nodes = self._selected_nodes()
        if not nodes:
            return
        kind, node = nodes[0]
        d = FolderDialog(self, node) if kind == "folder" else HostDialog(self, node)
        if d.run() == Gtk.ResponseType.OK:
            if kind == "folder":
                node.name = d.get_name()
            else:
                d.get_host()
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
        for kind, node in nodes:
            if kind == "folder":
                root.remove_folder(node.id)
            else:
                root.remove_host(node.id)
        self._changed()

    def _sort_tree(self):
        self.session_store.root.sort()
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

    def _open_terminal_tab(self, host: Host) -> TerminalTab:
        tab = TerminalTab(host, self.settings)
        tab.connect("user-input", self._on_tab_user_input)
        tab.connect("session-state-changed", lambda t: self._update_status())
        tab.connect("close-requested", self._close_tab)
        idx = self.notebook.append_page(tab, tab.tab_label)
        self.notebook.set_tab_reorderable(tab, True)
        self.notebook.set_menu_label_text(tab, host.name)
        self.notebook.set_current_page(idx)
        tab.connect_session()
        tab.terminal.grab_focus()
        self._status(f"Connecting to {host.name} ({host.target})…")
        return tab

    def _close_tab(self, tab: TerminalTab) -> bool:
        if tab.alive and self.settings.confirm_close_connected:
            if not confirm(self, f"Close {tab.host.name}?",
                           "The session is still connected and will be terminated.",
                           "_Close", destructive=True):
                return False
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
        for t in targets:
            t.send_line(text)
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
        else:
            ctx.remove_class("msm-broadcast-live")
            self._status("Live typing off.")

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
            info = load_about_info()
            sw = Gtk.ScrolledWindow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=32,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(info.logo_file), 128, 128, True)
                box.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
            except GLib.Error:
                pass
            name = Gtk.Label(label=info.app_name)
            name.get_style_context().add_class("msm-title")
            box.pack_start(name, False, False, 0)
            ver = Gtk.Label(label=f"Version {info.version}")
            ver.get_style_context().add_class("msm-dim")
            box.pack_start(ver, False, False, 0)
            if info.tagline:
                box.pack_start(Gtk.Label(label=info.tagline), False, False, 0)
            if info.author:
                a = Gtk.Label(label=f"By {info.author}")
                a.get_style_context().add_class("msm-dim")
                box.pack_start(a, False, False, 0)
            if info.link_url:
                box.pack_start(Gtk.LinkButton.new_with_label(info.link_url, info.link_label),
                               False, False, 6)
            lic = Gtk.Label(label=LICENSE_NOTICE, wrap=True, max_width_chars=72,
                            justify=Gtk.Justification.CENTER, selectable=True)
            lic.get_style_context().add_class("msm-dim")
            box.pack_start(lic, False, False, 16)
            sw.add(box)
            return sw

        self._special_tab("about", "About", build)

    # ================================================================
    # Preferences, status, shutdown
    # ================================================================
    def _on_preferences(self):
        d = PreferencesDialog(self, self.settings)
        if d.run() == Gtk.ResponseType.OK:
            d.apply_to(self.settings)
            self.settings.save()
            theme.apply_theme(self.settings.theme)
            for t in self._tabs():
                t.apply_settings(self.settings)
        d.destroy()

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
        for t in self._tabs():
            t.terminate()
        return True


class Application(Gtk.Application):
    def __init__(self, open_path: Optional[str] = None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.open_path = open_path
        self.settings = Settings.load()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name(APP_TITLE)
        theme.apply_theme(self.settings.theme)
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
        win.show_all()
        win._update_status()
        win.present()

    def _on_quit(self, *_):
        win = self.props.active_window
        if win is None or win.prepare_quit():
            self.quit()


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(f"usage: multissh-manager [SESSION_FILE]\n\n{APP_TITLE} {__version__}")
        return 0
    if len(argv) > 1 and argv[1] in ("-V", "--version"):
        print(__version__)
        return 0
    open_path = argv[1] if len(argv) > 1 else None
    return Application(open_path).run(argv[:1])


if __name__ == "__main__":
    sys.exit(main())

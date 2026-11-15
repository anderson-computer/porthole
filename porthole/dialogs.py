"""Add/edit dialogs and small message helpers."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from .models import AUTH_METHODS, PROTOCOLS, Folder, Host, local_user, parse_target
from .activity_log import default_log_path
from .secret_store import (BACKEND_KEYRING, BACKEND_LABELS, BACKEND_NONE, BACKEND_VAULT,
                           MIN_MASTER_LEN, default_vault_path)
from .settings import Settings, default_data_path

AUTH_LABELS = {
    "default": "Default (ssh config / agent / keys)",
    "key": "Specific private key",
    "agent": "SSH agent only",
    "password": "Password only",
}


def _textview(text: str, height: int = 60) -> Gtk.ScrolledWindow:
    tv = Gtk.TextView()
    tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    tv.get_buffer().set_text(text or "")
    tv.set_left_margin(4)
    tv.set_right_margin(4)
    sw = Gtk.ScrolledWindow()
    sw.set_shadow_type(Gtk.ShadowType.IN)
    sw.set_min_content_height(height)
    sw.add(tv)
    sw.textview = tv
    return sw


def _tv_text(sw) -> str:
    buf = sw.textview.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


class HostDialog(Gtk.Dialog):
    def __init__(self, parent, host: Optional[Host] = None,
                 password_store_label: Optional[str] = None, has_saved: bool = False):
        """password_store_label: where passwords would be saved, or None
        when password saving is turned off in Options."""
        super().__init__(
            title="Edit Host" if host else "Add Host",
            transient_for=parent, modal=True, destroy_with_parent=True,
        )
        self.set_default_size(520, -1)
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.ok_button = self.add_button("_Save", Gtk.ResponseType.OK)
        self.ok_button.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)
        self.host = host or Host()

        grid = Gtk.Grid(column_spacing=10, row_spacing=6, margin=12)
        self.get_content_area().add(grid)
        self._row = 0

        def add_row(label_text, widget, tip=None):
            lbl = Gtk.Label(label=label_text, xalign=1)
            lbl.get_style_context().add_class("msm-dim")
            grid.attach(lbl, 0, self._row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, self._row, 1, 1)
            if tip:
                widget.set_tooltip_text(tip)
            self._row += 1
            return widget

        h = self.host
        self.name_entry = add_row("Name", Gtk.Entry(text=h.name, activates_default=True))
        self.hostname_entry = add_row(
            "Host / IP", Gtk.Entry(text=h.hostname, activates_default=True,
                                   placeholder_text="server.example.com or ssh-config alias"))

        # Username gets its own labelled row (in MultiSSH Manager 2.2.0 it sat unlabelled to
        # the right of Port and was easy to miss).
        self.username_entry = add_row(
            "Username", Gtk.Entry(text=h.username, activates_default=True,
                                  placeholder_text=f"blank = ask when connecting (e.g. {local_user() or 'root'})"),
            "Login name on the remote host. Leave blank to be asked each time "
            "you connect. You can also type user@host in Host / IP.")
        self.port_spin = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self.port_spin.set_value(h.port or 22)
        port_box = Gtk.Box(spacing=6)
        port_box.pack_start(self.port_spin, False, False, 0)
        add_row("Port", port_box)

        self.protocol_combo = Gtk.ComboBoxText()
        for p in PROTOCOLS:
            self.protocol_combo.append(p, p)
        self.protocol_combo.set_active_id(h.protocol if h.protocol in PROTOCOLS else "ssh")
        add_row("Protocol", self.protocol_combo)

        self.auth_combo = Gtk.ComboBoxText()
        for a in AUTH_METHODS:
            self.auth_combo.append(a, AUTH_LABELS[a])
        self.auth_combo.set_active_id(h.auth_method if h.auth_method in AUTH_METHODS else "default")
        add_row("Authentication", self.auth_combo)

        key_box = Gtk.Box(spacing=4)
        self.key_entry = Gtk.Entry(text=h.private_key_path, placeholder_text="~/.ssh/id_ed25519")
        self.key_entry.set_hexpand(True)
        key_browse = Gtk.Button.new_from_icon_name("document-open-symbolic", Gtk.IconSize.BUTTON)
        key_browse.set_tooltip_text("Browse for a private key")
        key_browse.connect("clicked", self._on_browse_key)
        key_box.pack_start(self.key_entry, True, True, 0)
        key_box.pack_start(key_browse, False, False, 0)
        self.key_box = add_row("Private key", key_box)

        pw_box = Gtk.Box(spacing=6)
        self.password_entry = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD)
        self.password_entry.set_hexpand(True)
        self.password_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY,
                                                    "view-reveal-symbolic")
        self.password_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Show/hide")
        self.password_entry.connect(
            "icon-press", lambda e, *a: e.set_visibility(not e.get_visibility()))
        self.save_pw_check = Gtk.CheckButton(label="Save")
        self.has_saved = has_saved
        self.password_store_label = password_store_label
        if password_store_label is None:
            self.password_entry.set_placeholder_text(
                "Saving passwords is off (Edit ▸ Options ▸ Passwords)")
            self.password_entry.set_sensitive(False)
            self.save_pw_check.set_sensitive(False)
        else:
            self.password_entry.set_placeholder_text(
                "Saved — type to replace" if has_saved else "(optional) saved securely")
            self.save_pw_check.set_active(has_saved)
            self.save_pw_check.set_tooltip_text(f"Store in: {password_store_label}")
            self.password_entry.connect(
                "changed", lambda e: e.get_text() and self.save_pw_check.set_active(True))
        pw_box.pack_start(self.password_entry, True, True, 0)
        pw_box.pack_start(self.save_pw_check, False, False, 0)
        self.pw_box = add_row("Password", pw_box,
                              "Supplied to ssh automatically through a private askpass "
                              "helper; never typed into the terminal or written to "
                              "the session file. Untick Save to forget a saved password.")

        self.jump_entry = add_row(
            "Jump host", Gtk.Entry(text=h.jump_host, placeholder_text="user@bastion:22 (optional)"),
            "Passed to ssh -J. Comma-separate multiple hops.")
        self.extra_entry = add_row(
            "Extra ssh args", Gtk.Entry(text=h.extra_ssh_args, placeholder_text="-o ServerAliveInterval=30"),
            "Shell-style quoting is respected.")
        self.post_cmd = add_row(
            "Post-login", _textview(h.post_login_command, 50),
            "Sent once the remote shell has finished printing its login banner. "
            "One command per line.")
        self.notes = add_row("Notes", _textview(h.notes, 50))

        self.preview = Gtk.Label(xalign=0, selectable=True)
        self.preview.set_line_wrap(True)
        self.preview.set_line_wrap_mode(Pango.WrapMode.CHAR)
        self.preview.get_style_context().add_class("msm-dim")
        grid.attach(self.preview, 0, self._row, 2, 1)

        for w in (self.hostname_entry, self.username_entry, self.key_entry,
                  self.jump_entry, self.extra_entry):
            w.connect("changed", self._update)
        self.port_spin.connect("value-changed", self._update)
        self.protocol_combo.connect("changed", self._update)
        self.auth_combo.connect("changed", self._update)
        self._update()
        self.show_all()
        self.hostname_entry.grab_focus() if not h.hostname else self.name_entry.grab_focus()

    def _on_browse_key(self, _btn):
        dialog = Gtk.FileChooserNative.new("Select private key", self,
                                           Gtk.FileChooserAction.OPEN, "_Open", "_Cancel")
        ssh_dir = Path.home() / ".ssh"
        if ssh_dir.is_dir():
            dialog.set_current_folder(str(ssh_dir))
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            self.key_entry.set_text(dialog.get_filename())
        dialog.destroy()

    def _collect(self) -> Host:
        h = Host.from_dict(self.host.to_dict())
        h.hostname = self.hostname_entry.get_text().strip()
        h.username = self.username_entry.get_text().strip()
        try:
            user, host, port = parse_target(h.hostname)
        except ValueError:
            user, host, port = "", h.hostname, None
        if host and (user or port):
            h.hostname = host
            h.username = h.username or user
        h.name = self.name_entry.get_text().strip()
        if not h.name or h.name == "New Host":
            h.name = h.hostname or "New Host"
        h.port = int(self.port_spin.get_value())
        if host and port and h.port in (22, 23):
            h.port = port
        h.protocol = self.protocol_combo.get_active_id() or "ssh"
        h.auth_method = self.auth_combo.get_active_id() or "default"
        h.private_key_path = self.key_entry.get_text().strip()
        h.jump_host = self.jump_entry.get_text().strip()
        h.extra_ssh_args = self.extra_entry.get_text().strip()
        h.post_login_command = _tv_text(self.post_cmd).rstrip("\n")
        h.notes = _tv_text(self.notes).strip()
        return h

    def _update(self, *_):
        is_ssh = self.protocol_combo.get_active_id() == "ssh"
        self.auth_combo.set_sensitive(is_ssh)
        if self.password_store_label is not None:
            self.pw_box.set_sensitive(is_ssh and self.auth_combo.get_active_id()
                                      in ("default", "password"))
        self.key_box.set_sensitive(is_ssh and self.auth_combo.get_active_id() == "key")
        self.jump_entry.set_sensitive(is_ssh)
        self.extra_entry.set_sensitive(is_ssh)
        if self.port_spin.get_value() == 22 and not is_ssh:
            self.port_spin.set_value(23)
        elif self.port_spin.get_value() == 23 and is_ssh:
            self.port_spin.set_value(22)

        h = self._collect()
        error = None
        if not h.hostname:
            error = "Host / IP is required."
        else:
            try:
                shlex.split(h.extra_ssh_args)
            except ValueError as e:
                error = f"Extra ssh args: {e}"
        if error:
            self.preview.set_markup(f"<span foreground='#e5534b'>{error}</span>")
        else:
            text = "Runs: " + h.command_preview()
            if h.needs_username:
                text += "\n(No username saved: you'll be asked for one when you connect.)"
            self.preview.set_text(text)
        self.ok_button.set_sensitive(error is None)

    def get_host(self) -> Host:
        """Apply the form to the original Host object and return it."""
        new = self._collect()
        for f in Host.__dataclass_fields__:
            if f not in ("id", "save_password"):
                setattr(self.host, f, getattr(new, f))
        return self.host

    def password_action(self):
        """("set", pw) / ("forget", None) / (None, None) for no change."""
        if self.password_store_label is None or not self.pw_box.get_sensitive():
            return (None, None)
        text = self.password_entry.get_text()
        if self.save_pw_check.get_active():
            return ("set", text) if text else (None, None)
        return ("forget", None) if self.has_saved else (None, None)


class FolderDialog(Gtk.Dialog):
    def __init__(self, parent, folder: Optional[Folder] = None):
        super().__init__(title="Rename Folder" if folder else "Add Folder",
                         transient_for=parent, modal=True, destroy_with_parent=True)
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        ok = self.add_button("_Save", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)
        box = self.get_content_area()
        box.set_spacing(6)
        box.set_border_width(12)
        box.add(Gtk.Label(label="Folder name", xalign=0))
        self.name_entry = Gtk.Entry(text=folder.name if folder else "", activates_default=True,
                                    placeholder_text="New Folder")
        self.name_entry.set_width_chars(32)
        box.add(self.name_entry)
        self.show_all()

    def get_name(self) -> str:
        return self.name_entry.get_text().strip() or "New Folder"


class OptionsDialog(Gtk.Dialog):
    """Edit > Options. Every value is written to settings.json on Apply."""

    def __init__(self, parent, settings: Settings):
        super().__init__(title="Options", transient_for=parent, modal=True,
                         destroy_with_parent=True)
        self.set_default_size(560, -1)
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        ok = self.add_button("_Apply", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        self.notebook = Gtk.Notebook(margin=8)
        self.get_content_area().add(self.notebook)

        def section(title):
            grid = Gtk.Grid(column_spacing=10, row_spacing=8, margin=14)
            self.notebook.append_page(grid, Gtk.Label(label=title))
            return grid

        def note(text):
            lbl = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=70)
            lbl.get_style_context().add_class("msm-dim")
            return lbl

        def path_row(grid, row, label, entry, browse_cb, reset_cb):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, row, 1, 1)
            box = Gtk.Box(spacing=4)
            entry.set_hexpand(True)
            box.pack_start(entry, True, True, 0)
            b = Gtk.Button.new_from_icon_name("document-open-symbolic", Gtk.IconSize.BUTTON)
            b.set_tooltip_text("Choose location…")
            b.connect("clicked", lambda _b: browse_cb())
            box.pack_start(b, False, False, 0)
            r = Gtk.Button.new_from_icon_name("edit-undo-symbolic", Gtk.IconSize.BUTTON)
            r.set_tooltip_text("Reset to default")
            r.connect("clicked", lambda _b: reset_cb())
            box.pack_start(r, False, False, 0)
            grid.attach(box, 1, row, 1, 1)

        # --- Data file -------------------------------------------------
        g = section("Data File")
        self.data_entry = Gtk.Entry(text=str(settings.data_path))
        path_row(g, 0, "Location", self.data_entry,
                 lambda: self._browse(self.data_entry, "Choose session data file",
                                      "sessions.json", "*.json"),
                 lambda: self.data_entry.set_text(str(default_data_path())))
        g.attach(note("Your folders and hosts are saved here and loaded on startup. "
                      "If you pick a file that already exists you'll be asked whether to "
                      "open it or overwrite it with your current sessions."), 1, 1, 1, 1)

        # --- Activity log ----------------------------------------------
        g = section("Activity Log")
        self.log_check = Gtk.CheckButton(label="Append activity to a log file")
        self.log_check.set_active(settings.log_enabled)
        g.attach(self.log_check, 0, 0, 2, 1)
        self.log_entry = Gtk.Entry(text=settings.log_file or str(default_log_path()))
        path_row(g, 1, "Log file", self.log_entry,
                 lambda: self._browse(self.log_entry, "Choose activity log file",
                                      "activity.log", "*.log"),
                 lambda: self.log_entry.set_text(str(default_log_path())))
        g.attach(note("Logs sessions opened and closed, edits, imports, saves, broadcast "
                      "commands and option changes. Never logs keystrokes typed into "
                      "terminals, terminal output, or password values: secrets in "
                      "commands are replaced with ***, and anything broadcast to a "
                      "password prompt is not recorded."), 1, 2, 1, 1)
        self.log_check.connect("toggled", lambda c: self._sync_log_sensitivity())
        self._log_widgets = [w for w in g.get_children() if w is not self.log_check]
        self._sync_log_sensitivity()

        # --- Passwords ---------------------------------------------------
        g = section("Passwords")
        self.backend = Gtk.ComboBoxText()
        for key, label in BACKEND_LABELS.items():
            self.backend.append(key, label)
        self.backend.set_active_id(settings.password_backend)
        g.attach(Gtk.Label(label="Store passwords", xalign=1), 0, 0, 1, 1)
        g.attach(self.backend, 1, 0, 1, 1)
        self.vault_entry = Gtk.Entry(text=settings.vault_file or str(default_vault_path()))
        path_row(g, 1, "Vault file", self.vault_entry,
                 lambda: self._browse(self.vault_entry, "Choose vault file",
                                      "vault.json", "*.json"),
                 lambda: self.vault_entry.set_text(str(default_vault_path())))
        self.vault_widgets = [c for c in g.get_children()
                              if g.child_get_property(c, "top-attach") == 1]
        self.change_master_btn = Gtk.Button(label="Change Master Password…")
        self.change_master_btn.set_halign(Gtk.Align.START)
        g.attach(self.change_master_btn, 1, 2, 1, 1)
        g.attach(note("Keychain: stored in your login keyring, unlocked when you log in. "
                      "Vault: an AES-256 encrypted file, unlocked with a master password "
                      "once per session — works without a desktop keyring. Saved "
                      "passwords are handed to ssh through a private helper and are never "
                      "typed into the terminal, written to the session file, or logged."),
                 1, 3, 1, 1)
        self.backend.connect("changed", lambda c: self._sync_backend())
        self._sync_backend()

        # --- Terminal & appearance --------------------------------------
        g = section("Terminal")
        self.font_btn = Gtk.FontButton(font=settings.font)
        self.font_btn.set_filter_func(lambda fam, face: fam.is_monospace())
        self.scrollback = Gtk.SpinButton.new_with_range(0, 1000000, 1000)
        self.scrollback.set_value(settings.scrollback_lines)
        self.theme = Gtk.ComboBoxText()
        self.theme.append("dark", "Built-in dark")
        self.theme.append("system", "System GTK theme")
        self.theme.set_active_id(settings.theme)
        self.idle = Gtk.SpinButton.new_with_range(200, 10000, 100)
        self.idle.set_value(settings.post_login_idle_ms)
        self.idle.set_tooltip_text(
            "How long the terminal must be quiet at a shell prompt before the "
            "post-login command is sent.")
        rows = [("Font", self.font_btn), ("Scrollback lines", self.scrollback),
                ("Theme", self.theme), ("Post-login delay (ms)", self.idle)]
        for i, (label, w) in enumerate(rows):
            g.attach(Gtk.Label(label=label, xalign=1), 0, i, 1, 1)
            w.set_halign(Gtk.Align.START)
            g.attach(w, 1, i, 1, 1)
        self.confirm = Gtk.CheckButton(label="Confirm before closing a connected tab")
        self.confirm.set_active(settings.confirm_close_connected)
        self.restore = Gtk.CheckButton(label="Reopen last session's tabs on startup")
        self.restore.set_active(settings.restore_tabs)
        g.attach(self.confirm, 0, len(rows), 2, 1)
        g.attach(self.restore, 0, len(rows) + 1, 2, 1)
        self.show_all()

    def _sync_backend(self):
        is_vault = self.backend.get_active_id() == BACKEND_VAULT
        for w in self.vault_widgets + [self.change_master_btn]:
            w.set_sensitive(is_vault)

    def vault_path(self) -> Path:
        text = self.vault_entry.get_text().strip()
        return Path(text).expanduser() if text else default_vault_path()

    def _sync_log_sensitivity(self):
        for w in self._log_widgets:
            w.set_sensitive(self.log_check.get_active())

    def _browse(self, entry, title, default_name, pattern):
        dialog = Gtk.FileChooserNative.new(title, self, Gtk.FileChooserAction.SAVE,
                                           "_Select", "_Cancel")
        current = Path(entry.get_text()).expanduser()
        if current.parent.is_dir():
            dialog.set_current_folder(str(current.parent))
        dialog.set_current_name(current.name or default_name)
        f = Gtk.FileFilter()
        f.add_pattern(pattern)
        f.set_name(pattern)
        dialog.add_filter(f)
        any_f = Gtk.FileFilter()
        any_f.add_pattern("*")
        any_f.set_name("All files")
        dialog.add_filter(any_f)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            entry.set_text(dialog.get_filename())
        dialog.destroy()

    def data_path(self) -> Path:
        text = self.data_entry.get_text().strip()
        return Path(text).expanduser() if text else default_data_path()

    def log_path(self) -> Path:
        text = self.log_entry.get_text().strip()
        return Path(text).expanduser() if text else default_log_path()

    def apply_to(self, s: Settings) -> None:
        """Copy everything except the data file (the caller handles moving
        or opening that)."""
        s.font = self.font_btn.get_font()
        s.scrollback_lines = int(self.scrollback.get_value())
        s.theme = self.theme.get_active_id() or "dark"
        s.post_login_idle_ms = int(self.idle.get_value())
        s.confirm_close_connected = self.confirm.get_active()
        s.restore_tabs = self.restore.get_active()
        s.log_enabled = self.log_check.get_active()
        vp = self.vault_path()
        s.vault_file = "" if vp == default_vault_path() else str(vp)
        lp = self.log_path()
        s.log_file = "" if lp == default_log_path() else str(lp)


class MasterPasswordDialog(Gtk.Dialog):
    """mode: "create", "unlock" or "change"."""

    def __init__(self, parent, mode: str, vault_path: Path, message: str = ""):
        titles = {"create": "Create Password Vault", "unlock": "Unlock Password Vault",
                  "change": "Change Master Password"}
        super().__init__(title=titles[mode], transient_for=parent, modal=True,
                         destroy_with_parent=True)
        self.mode = mode
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.ok = self.add_button("_Unlock" if mode == "unlock" else "_Save",
                                  Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=14)
        self.get_content_area().add(box)
        intro = {
            "create": "Choose a master password for your new vault. It encrypts every "
                      "saved host password and cannot be recovered if forgotten.",
            "unlock": "Enter the master password to unlock saved host passwords "
                      "for this session.",
            "change": "Enter the current master password and a new one.",
        }[mode]
        lbl = Gtk.Label(label=intro, xalign=0, wrap=True, max_width_chars=56)
        box.pack_start(lbl, False, False, 0)
        path_lbl = Gtk.Label(label=str(vault_path), xalign=0, selectable=True)
        path_lbl.get_style_context().add_class("msm-dim")
        box.pack_start(path_lbl, False, False, 0)
        if message:
            m = Gtk.Label(xalign=0, wrap=True, max_width_chars=56)
            m.set_markup(f"<span foreground='#e5534b'>{GLib.markup_escape_text(message)}</span>")
            box.pack_start(m, False, False, 0)

        def pw(placeholder):
            e = Gtk.Entry(visibility=False, activates_default=True,
                          input_purpose=Gtk.InputPurpose.PASSWORD,
                          placeholder_text=placeholder)
            e.connect("changed", lambda _e: self._validate())
            box.pack_start(e, False, False, 0)
            return e

        self.current = pw("Current master password") if mode in ("unlock", "change") else None
        if mode in ("create", "change"):
            self.new1 = pw(f"New master password (min. {MIN_MASTER_LEN} characters)")
            self.new2 = pw("Repeat new master password")
        else:
            self.new1 = self.new2 = None
        self.hint = Gtk.Label(xalign=0)
        self.hint.get_style_context().add_class("msm-dim")
        box.pack_start(self.hint, False, False, 0)
        self._validate()
        self.show_all()

    def _validate(self):
        ok, hint = True, ""
        if self.current is not None and not self.current.get_text():
            ok = False
        if self.new1 is not None:
            a, b = self.new1.get_text(), self.new2.get_text()
            if len(a) < MIN_MASTER_LEN:
                ok, hint = False, f"At least {MIN_MASTER_LEN} characters."
            elif a != b:
                ok, hint = False, "Passwords don't match."
        self.hint.set_text(hint)
        self.ok.set_sensitive(ok)

    def current_password(self) -> str:
        return self.current.get_text() if self.current else ""

    def new_password(self) -> str:
        return self.new1.get_text() if self.new1 else ""


class UsernameDialog(Gtk.Dialog):
    """Asks for the login name for a host that has none saved."""

    def __init__(self, parent, host_label: str, default_user: str = "",
                 can_remember: bool = True):
        super().__init__(title=f"Username for {host_label}", transient_for=parent,
                         modal=True, destroy_with_parent=True)
        self.set_default_size(420, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=14)
        self.get_content_area().add(box)
        lbl = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        lbl.set_markup(f"No username is saved for <b>{GLib.markup_escape_text(host_label)}</b>. "
                       "Log in as:")
        box.pack_start(lbl, False, False, 0)
        self.entry = Gtk.Entry(text=default_user, activates_default=True,
                               placeholder_text="username")
        box.pack_start(self.entry, False, False, 0)
        self.remember_check = None
        if can_remember:
            self.remember_check = Gtk.CheckButton(label="Save this username for the host")
            self.remember_check.set_active(True)
            box.pack_start(self.remember_check, False, False, 0)
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.ok = self.add_button("_Connect", Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)
        self.entry.connect("changed", lambda e: self.ok.set_sensitive(self._valid()))
        self.ok.set_sensitive(self._valid())
        self.show_all()
        self.entry.grab_focus()

    def _valid(self) -> bool:
        t = self.entry.get_text().strip()
        return bool(t) and not t.startswith("-") and not any(c.isspace() or c == "@" for c in t)

    def get_user(self) -> Optional[str]:
        return self.entry.get_text().strip() if self._valid() else None

    @property
    def remember(self) -> bool:
        return bool(self.remember_check and self.remember_check.get_active())


class AskpassDialog(Gtk.Dialog):
    """Shows a prompt from ssh (password, passphrase, host key, OTP...)."""

    def __init__(self, parent, host_name: str, prompt: str, kind: str,
                 save_label: Optional[str] = None, note_text: str = ""):
        super().__init__(title=f"{host_name} — ssh", transient_for=parent, modal=True,
                         destroy_with_parent=True)
        self.set_default_size(460, -1)
        lower = prompt.lower()
        self.is_confirm = kind == "confirm" or "(yes/no" in lower
        self.is_info = kind == "none"
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=14)
        self.get_content_area().add(box)
        if note_text:
            n = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
            n.set_markup(f"<span foreground='#e0b04a'>{GLib.markup_escape_text(note_text)}</span>")
            box.pack_start(n, False, False, 0)
        lbl = Gtk.Label(label=prompt.strip(), xalign=0, wrap=True, max_width_chars=70,
                        selectable=True)
        box.pack_start(lbl, False, False, 0)
        self.entry = None
        self.save_check = None
        if self.is_info:
            self.add_button("_OK", Gtk.ResponseType.OK)
        elif self.is_confirm:
            self.add_button("_No", Gtk.ResponseType.NO)
            yes = self.add_button("_Yes", Gtk.ResponseType.YES)
            yes.get_style_context().add_class("suggested-action")
        else:
            self.entry = Gtk.Entry(visibility=False, activates_default=True,
                                   input_purpose=Gtk.InputPurpose.PASSWORD)
            box.pack_start(self.entry, False, False, 0)
            if save_label:
                self.save_check = Gtk.CheckButton(label=f"Save password ({save_label})")
                box.pack_start(self.save_check, False, False, 0)
            self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
            ok = self.add_button("_OK", Gtk.ResponseType.OK)
            ok.get_style_context().add_class("suggested-action")
            self.set_default_response(Gtk.ResponseType.OK)
        self.show_all()

    def answer(self) -> Optional[str]:
        """Runs the dialog; returns the text for ssh or None if cancelled."""
        resp = self.run()
        if self.is_info:
            return ""
        if self.is_confirm:
            return "yes" if resp == Gtk.ResponseType.YES else "no"
        return self.entry.get_text() if resp == Gtk.ResponseType.OK else None

    @property
    def wants_save(self) -> bool:
        return bool(self.save_check and self.save_check.get_active())


def confirm(parent, text: str, secondary: str = "", ok_label: str = "_OK",
            destructive: bool = False) -> bool:
    d = Gtk.MessageDialog(transient_for=parent, modal=True,
                          message_type=Gtk.MessageType.QUESTION,
                          buttons=Gtk.ButtonsType.NONE, text=text)
    if secondary:
        d.format_secondary_text(secondary)
    d.add_button("_Cancel", Gtk.ResponseType.CANCEL)
    ok = d.add_button(ok_label, Gtk.ResponseType.OK)
    ok.get_style_context().add_class("destructive-action" if destructive else "suggested-action")
    resp = d.run()
    d.destroy()
    return resp == Gtk.ResponseType.OK


def error(parent, text: str, secondary: str = "") -> None:
    d = Gtk.MessageDialog(transient_for=parent, modal=True,
                          message_type=Gtk.MessageType.ERROR,
                          buttons=Gtk.ButtonsType.CLOSE, text=text)
    if secondary:
        d.format_secondary_text(secondary)
    d.run()
    d.destroy()

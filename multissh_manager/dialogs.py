"""Add/edit dialogs and small message helpers."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Pango  # noqa: E402

from .models import AUTH_METHODS, PROTOCOLS, Folder, Host
from .settings import Settings

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
    def __init__(self, parent, host: Optional[Host] = None):
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

        port_user = Gtk.Box(spacing=6)
        self.port_spin = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self.port_spin.set_value(h.port or 22)
        self.username_entry = Gtk.Entry(text=h.username, activates_default=True,
                                        placeholder_text="(ssh default)")
        self.username_entry.set_hexpand(True)
        port_user.pack_start(self.port_spin, False, False, 0)
        port_user.pack_start(Gtk.Label(label="User"), False, False, 4)
        port_user.pack_start(self.username_entry, True, True, 0)
        add_row("Port", port_user)

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
        h.name = self.name_entry.get_text().strip()
        if not h.name or h.name == "New Host":
            h.name = h.hostname or "New Host"
        h.port = int(self.port_spin.get_value())
        h.username = self.username_entry.get_text().strip()
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
            self.preview.set_text("Runs: " + h.command_preview())
        self.ok_button.set_sensitive(error is None)

    def get_host(self) -> Host:
        """Apply the form to the original Host object and return it."""
        new = self._collect()
        for f in Host.__dataclass_fields__:
            if f != "id":
                setattr(self.host, f, getattr(new, f))
        return self.host


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


class PreferencesDialog(Gtk.Dialog):
    def __init__(self, parent, settings: Settings):
        super().__init__(title="Preferences", transient_for=parent, modal=True,
                         destroy_with_parent=True)
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        ok = self.add_button("_Apply", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        grid = Gtk.Grid(column_spacing=10, row_spacing=8, margin=12)
        self.get_content_area().add(grid)

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
            "How long the terminal must be quiet after login before the "
            "post-login command is sent.")
        self.confirm = Gtk.CheckButton(label="Confirm before closing a connected tab")
        self.confirm.set_active(settings.confirm_close_connected)
        self.restore = Gtk.CheckButton(label="Reopen last session's tabs on startup")
        self.restore.set_active(settings.restore_tabs)

        rows = [("Terminal font", self.font_btn), ("Scrollback lines", self.scrollback),
                ("Theme", self.theme), ("Post-login delay (ms)", self.idle)]
        for i, (label, w) in enumerate(rows):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, i, 1, 1)
            grid.attach(w, 1, i, 1, 1)
        grid.attach(self.confirm, 0, len(rows), 2, 1)
        grid.attach(self.restore, 0, len(rows) + 1, 2, 1)
        self.show_all()

    def apply_to(self, s: Settings) -> None:
        s.font = self.font_btn.get_font()
        s.scrollback_lines = int(self.scrollback.get_value())
        s.theme = self.theme.get_active_id() or "dark"
        s.post_login_idle_ms = int(self.idle.get_value())
        s.confirm_close_connected = self.confirm.get_active()
        s.restore_tabs = self.restore.get_active()


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

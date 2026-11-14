"""A notebook page holding one live session: VTE terminal + status bar."""

from __future__ import annotations

import os
import shutil
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango, Vte  # noqa: E402

from .models import Host
from .settings import Settings

STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_CLOSED = "closed"

_STATE_CLASS = {
    STATE_CONNECTING: "msm-state-wait",
    STATE_CONNECTED: "msm-state-ok",
    STATE_CLOSED: "msm-state-dead",
}


class TerminalTab(Gtk.Box):
    __gsignals__ = {
        # Emitted with the text the user typed into this terminal.
        "user-input": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "session-state-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "close-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, host: Host, settings: Settings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.host = host
        self.settings = settings
        self.state = STATE_CONNECTING
        self.pid: Optional[int] = None
        self._post_login_pending = False
        self._post_login_timer = 0
        self._reconnect_after_exit = False
        self._feeding = False  # VTE emits "commit" for feed_child() too

        # --- disconnected banner -------------------------------------
        self.infobar = Gtk.InfoBar()
        self.infobar.set_message_type(Gtk.MessageType.WARNING)
        self.infobar.set_no_show_all(True)
        self.info_label = Gtk.Label(xalign=0)
        self.infobar.get_content_area().add(self.info_label)
        self.info_label.show()
        self.infobar.add_button("_Reconnect", 1)
        self.infobar.add_button("_Close Tab", 2)
        self.infobar.connect("response", self._on_infobar_response)
        self.pack_start(self.infobar, False, False, 0)

        # --- terminal --------------------------------------------------
        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(settings.scrollback_lines)
        self.terminal.set_mouse_autohide(True)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_audible_bell(False)
        self.apply_settings(settings)
        self.terminal.connect("child-exited", self._on_child_exited)
        self.terminal.connect("contents-changed", self._on_contents_changed)
        self.terminal.connect("commit", self._on_commit)
        self.terminal.connect("button-press-event", self._on_button_press)
        self.terminal.connect("key-press-event", self._on_key_press)
        self.terminal.connect("window-title-changed", lambda t: self._update_tab_label())

        box = Gtk.Box()
        box.pack_start(self.terminal, True, True, 0)
        scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL,
                                  adjustment=self.terminal.get_vadjustment())
        box.pack_start(scrollbar, False, False, 0)
        self.pack_start(box, True, True, 0)

        # --- tab label -------------------------------------------------
        self.tab_label = Gtk.Box(spacing=4)
        self.state_dot = Gtk.Label(label="●")
        self.name_label = Gtk.Label(label=host.name)
        self.name_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.name_label.set_width_chars(min(len(host.name), 24))
        self.name_label.set_max_width_chars(24)
        self.bcast_toggle = Gtk.CheckButton()
        self.bcast_toggle.set_active(True)
        self.bcast_toggle.set_tooltip_text("Include this tab in broadcasts (Ctrl+Shift+B)")
        self.bcast_toggle.set_can_focus(False)
        self.bcast_toggle.connect("toggled", lambda b: self._on_broadcast_toggled())
        close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.set_can_focus(False)
        close_btn.set_tooltip_text("Close tab (Ctrl+Shift+W)")
        close_btn.connect("clicked", lambda b: self.emit("close-requested"))
        for w in (self.bcast_toggle, self.state_dot, self.name_label):
            self.tab_label.pack_start(w, False, False, 0)
        self.tab_label.pack_start(close_btn, False, False, 0)
        self.tab_label.show_all()

        self.show_all()
        self._update_tab_label()

    # ------------------------------------------------------------------
    @property
    def broadcast(self) -> bool:
        return self.bcast_toggle.get_active()

    @broadcast.setter
    def broadcast(self, value: bool) -> None:
        self.bcast_toggle.set_active(value)

    @property
    def alive(self) -> bool:
        return self.state != STATE_CLOSED

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.terminal.set_font(Pango.FontDescription.from_string(settings.font))
        self.terminal.set_scrollback_lines(settings.scrollback_lines)

    # ------------------------------------------------------------------
    def connect_session(self) -> None:
        argv = self.host.build_argv()
        if not shutil.which(argv[0]):
            self._set_closed(f"'{argv[0]}' is not installed on this system.")
            return
        self._set_state(STATE_CONNECTING)
        self.infobar.hide()
        self._post_login_pending = bool(self.host.post_login_command.strip())
        self.terminal.feed(
            f"\x1b[2m$ {self.host.command_preview()}\x1b[0m\r\n".encode("utf-8"))

        def on_spawned(term, pid, error, *_):
            if error is not None or pid == -1:
                self._set_closed(f"Could not start session: {error.message if error else 'unknown error'}")
                return
            self.pid = pid

        try:
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT, os.environ.get("HOME", "/"), argv, None,
                GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None, on_spawned)
        except (TypeError, AttributeError):
            try:
                ok, pid = self.terminal.spawn_sync(
                    Vte.PtyFlags.DEFAULT, os.environ.get("HOME", "/"), argv, None,
                    GLib.SpawnFlags.SEARCH_PATH, None, None, None)
                self.pid = pid
            except GLib.Error as e:
                self._set_closed(f"Could not start session: {e.message}")

    def reconnect(self) -> None:
        if self.alive and self.pid:
            try:
                os.kill(self.pid, 15)
            except OSError:
                pass
            # child-exited will fire; reconnect once it does.
            self._reconnect_after_exit = True
            return
        self.terminal.feed(b"\r\n")
        self.connect_session()

    def terminate(self) -> None:
        self._cancel_post_login()
        if self.alive and self.pid:
            try:
                os.kill(self.pid, 1)  # SIGHUP, like closing a terminal window
            except OSError:
                pass

    def send_text(self, text: str) -> bool:
        if not self.alive:
            return False
        self._feeding = True
        try:
            self.terminal.feed_child(text.encode("utf-8"))
        finally:
            self._feeding = False
        return True

    def send_line(self, line: str) -> bool:
        return self.send_text(line + "\r")

    def copy(self) -> None:
        if self.terminal.get_has_selection():
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)

    def paste(self) -> None:
        if self.alive:
            self.terminal.paste_clipboard()

    def zoom(self, factor: Optional[float]) -> None:
        if factor is None:
            self.terminal.set_font_scale(1.0)
        else:
            scale = self.terminal.get_font_scale() * factor
            self.terminal.set_font_scale(max(0.4, min(scale, 4.0)))

    # ------------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self.state = state
        self._update_tab_label()
        self.emit("session-state-changed")

    def _set_closed(self, message: str) -> None:
        self.pid = None
        self._cancel_post_login()
        self._set_state(STATE_CLOSED)
        self.info_label.set_text(message)
        self.infobar.show()
        self.terminal.feed(
            f"\r\n\x1b[33m[{message}  Press Enter to reconnect.]\x1b[0m\r\n".encode("utf-8"))

    def _update_tab_label(self) -> None:
        ctx = self.state_dot.get_style_context()
        for cls in _STATE_CLASS.values():
            ctx.remove_class(cls)
        ctx.add_class(_STATE_CLASS[self.state])
        title = self.terminal.get_window_title() or ""
        tip = f"{self.host.name}\n{self.host.target}" + (f"\n{title}" if title else "")
        tip += f"\nState: {self.state}"
        self.tab_label.set_tooltip_text(tip)
        self.state_dot.set_tooltip_text(self.state.capitalize())

    def _on_broadcast_toggled(self) -> None:
        ctx = self.name_label.get_style_context()
        if self.broadcast:
            ctx.add_class("msm-tab-bcast")
        else:
            ctx.remove_class("msm-tab-bcast")
        self.emit("session-state-changed")

    def _on_child_exited(self, _term, status: int) -> None:
        if self._reconnect_after_exit:
            self._reconnect_after_exit = False
            self.pid = None
            self.state = STATE_CLOSED
            self.terminal.feed(b"\r\n")
            self.connect_session()
            return
        code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else None
        if code == 0:
            msg = "Session ended."
        elif code is not None:
            msg = f"Session ended with exit status {code}."
        else:
            msg = "Session was terminated."
        self._set_closed(msg)

    def _on_contents_changed(self, _term) -> None:
        if self.state == STATE_CONNECTING:
            self._set_state(STATE_CONNECTED)
        if self._post_login_pending:
            # Wait until output goes quiet (banner/MOTD/prompt finished).
            if self._post_login_timer:
                GLib.source_remove(self._post_login_timer)
            self._post_login_timer = GLib.timeout_add(
                self.settings.post_login_idle_ms, self._send_post_login)

    def _last_line(self) -> str:
        try:
            text = self.terminal.get_text(None, None)
        except TypeError:
            text = self.terminal.get_text(None)
        if isinstance(text, tuple):
            text = text[0]
        lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    def _at_shell_prompt(self) -> bool:
        """True when the last line looks like a shell prompt rather than a
        host-key, password, passphrase or MFA question."""
        line = self._last_line()
        lower = line.lower()
        if any(w in lower for w in ("password", "passphrase", "yes/no", "verification",
                                    "code:", "token", "fingerprint")):
            return False
        return line.endswith(("$", "#", ">", "%", "]"))

    def _send_post_login(self) -> bool:
        self._post_login_timer = 0
        if self._post_login_pending and self.alive and self._at_shell_prompt():
            self._post_login_pending = False
            for line in self.host.post_login_command.splitlines():
                if line.strip():
                    self.send_line(line)
        return False

    def _cancel_post_login(self) -> None:
        self._post_login_pending = False
        if self._post_login_timer:
            GLib.source_remove(self._post_login_timer)
            self._post_login_timer = 0

    def _on_commit(self, _term, text: str, _size: int) -> None:
        # Only real keyboard input is reported; programmatic sends
        # (broadcast, post-login, mirroring) must not be re-mirrored.
        if self.alive and not self._feeding:
            self.emit("user-input", text)

    def _on_key_press(self, _term, event) -> bool:
        if not self.alive and event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.reconnect()
            return True
        return False

    def _on_infobar_response(self, _bar, response: int) -> None:
        if response == 1:
            self.reconnect()
        elif response == 2:
            self.emit("close-requested")

    def _on_button_press(self, _term, event) -> bool:
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 3:
            return False
        menu = Gtk.Menu()

        def item(label, cb, sensitive=True):
            mi = Gtk.MenuItem.new_with_mnemonic(label)
            mi.set_sensitive(sensitive)
            mi.connect("activate", lambda w: cb())
            menu.append(mi)

        item("_Copy", self.copy, self.terminal.get_has_selection())
        item("_Paste", self.paste, self.alive)
        item("Select _All", self.terminal.select_all)
        menu.append(Gtk.SeparatorMenuItem())
        item("_Reconnect", self.reconnect)
        item("Close _Tab", lambda: self.emit("close-requested"))
        menu.attach_to_widget(self.terminal, None)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

"""Applies the bundled dark GTK3 theme (or leaves the system theme alone)."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
CSS_FILE = RESOURCES_DIR / "dark.css"
LOGO_FILE = RESOURCES_DIR / "logo.svg"

_provider = None


def apply_theme(name: str = "dark") -> None:
    """name: "dark" (bundled stylesheet) or "system" (GTK theme as-is)."""
    global _provider
    screen = Gdk.Screen.get_default()
    settings = Gtk.Settings.get_default()
    if _provider is not None and screen is not None:
        Gtk.StyleContext.remove_provider_for_screen(screen, _provider)
        _provider = None
    if settings is not None:
        settings.set_property("gtk-application-prefer-dark-theme", name == "dark")
    if name != "dark" or not CSS_FILE.exists() or screen is None:
        return
    _provider = Gtk.CssProvider()
    _provider.load_from_path(str(CSS_FILE))
    Gtk.StyleContext.add_provider_for_screen(
        screen, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

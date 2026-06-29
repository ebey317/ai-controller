#!/usr/bin/env python3
"""
ai-controller-launcher.py — Simple GTK launcher and updater for AI Controller.

Run from the desktop or bind to a controller button:
    python3 "$HOME/ai-controller/scripts/ai-controller-launcher.py"

Features:
- Start / stop AI Controller services
- Check for and install updates
- Show current version and service status
"""
import os
import shutil
import subprocess
import sys

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INSTALL_DIR = os.path.dirname(SCRIPT_DIR)
VERSION_FILE = os.path.join(INSTALL_DIR, "VERSION")
UPDATE_SCRIPT = os.path.join(SCRIPT_DIR, "update.sh")
CONFIG_DIR = os.path.expanduser("~/.config/ai-controller")
LOCK_FILE = os.path.join(CONFIG_DIR, "lock_desktop_profile")
os.makedirs(CONFIG_DIR, exist_ok=True)

SERVICES = [
    "antimicrox-autoload.service",
    "voice-bridge.service",
    "ptt-pynput.service",
    "controller-legend.service",
    "ai-slide-keyboard.service",
]

HUD_ORANGE = "#FF6A00"

CSS = b"""
window { background-color: rgba(13,13,18,0.94); }
#panel {
    background-color: rgba(13,13,18,0.94);
    border: 2px solid #FF6A00;
    border-radius: 16px;
    padding: 16px;
}
button {
    background-image: none;
    background-color: #23232b;
    color: #e8e8e8;
    border: 1px solid #3a3a44;
    border-radius: 8px;
    font-size: 14px;
    padding: 10px 16px;
}
button:hover { background-color: #2f2f3a; }
button.action { background-color: #2a1a0a; color: #FF6A00; border-color: #FF6A00; font-weight: bold; }
button.danger { background-color: #2a0a0a; color: #ff4a4a; border-color: #ff4a4a; font-weight: bold; }
label { color: #e8e8e8; font-size: 13px; }
label.title { color: #FF6A00; font-size: 18px; font-weight: bold; }
label.status { color: #a0a0a8; font-size: 12px; }
"""


class Launcher(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("AI Controller")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_default_size(320, 280)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.set_name("panel")
        self.add(panel)

        title = Gtk.Label(label="AI CONTROLLER")
        title.get_style_context().add_class("title")
        panel.pack_start(title, False, False, 0)

        self.version_label = Gtk.Label(label=self._local_version())
        self.version_label.get_style_context().add_class("status")
        panel.pack_start(self.version_label, False, False, 0)

        self.status_label = Gtk.Label(label="Checking status...")
        self.status_label.get_style_context().add_class("status")
        panel.pack_start(self.status_label, False, False, 0)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.pack_start(btn_box, True, True, 0)

        self.start_btn = Gtk.Button(label="Start AI Controller")
        self.start_btn.get_style_context().add_class("action")
        self.start_btn.connect("clicked", self._on_start)
        btn_box.pack_start(self.start_btn, False, False, 0)

        self.stop_btn = Gtk.Button(label="Stop AI Controller")
        self.stop_btn.get_style_context().add_class("danger")
        self.stop_btn.connect("clicked", self._on_stop)
        btn_box.pack_start(self.stop_btn, False, False, 0)

        self.lock_btn = Gtk.Button(label="Lock AI Desktop Profile")
        self.lock_btn.connect("clicked", self._on_lock)
        btn_box.pack_start(self.lock_btn, False, False, 0)

        self.unlock_btn = Gtk.Button(label="Unlock Profile Switching")
        self.unlock_btn.connect("clicked", self._on_unlock)
        btn_box.pack_start(self.unlock_btn, False, False, 0)

        update_btn = Gtk.Button(label="Check for Updates")
        update_btn.get_style_context().add_class("action")
        update_btn.connect("clicked", self._on_update)
        btn_box.pack_start(update_btn, False, False, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda *_: Gtk.main_quit())
        btn_box.pack_end(close_btn, False, False, 0)

        self.connect("destroy", Gtk.main_quit)
        self.show_all()

        GLib.timeout_add(1000, self._refresh_status)

        # If launched from desktop/autostart, ensure services are running.
        if os.environ.get("AI_CONTROLLER_AUTOSTART") == "1":
            self._on_start(None)

    def _local_version(self):
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                return f"Version {f.read().strip()}"
        except Exception:
            return "Version unknown"

    def _service_status(self):
        running = 0
        for svc in SERVICES:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", svc],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                running += 1
        locked = " | Desktop LOCKED" if os.path.exists(LOCK_FILE) else ""
        return f"Services: {running}/{len(SERVICES)} running{locked}"

    def _refresh_status(self):
        self.status_label.set_text(self._service_status())
        return True

    def _on_start(self, _widget):
        subprocess.Popen(
            ["systemctl", "--user", "start"] + SERVICES,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.status_label.set_text("Starting services...")

    def _on_stop(self, _widget):
        subprocess.Popen(
            ["systemctl", "--user", "stop"] + SERVICES,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.status_label.set_text("Stopping services...")

    def _on_lock(self, _widget):
        open(LOCK_FILE, "w").close()
        self.status_label.set_text("AI Desktop profile locked.")

    def _on_unlock(self, _widget):
        try:
            os.remove(LOCK_FILE)
        except FileNotFoundError:
            pass
        self.status_label.set_text("Profile switching unlocked.")

    def _on_update(self, _widget):
        if not os.path.exists(UPDATE_SCRIPT):
            self.status_label.set_text("Update script not found.")
            return
        self.status_label.set_text("Checking for updates...")
        subprocess.Popen(["x-terminal-emulator", "-e", UPDATE_SCRIPT] if shutil.which("x-terminal-emulator") else ["bash", UPDATE_SCRIPT],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    Launcher()
    Gtk.main()

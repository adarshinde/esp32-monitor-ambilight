#!/usr/bin/env python3
"""macOS menu bar app for the ESP32 LED strip. Thin GUI wrapper around the
existing `ledctl` CLI tool — reuses it for every action so mode-switching
and screen_sync.py lifecycle logic lives in exactly one place."""

import json
import subprocess

import rumps

LEDCTL = "/Users/adarsh/.local/bin/ledctl"

MODES = [
    (1, "Warm Sweep"),
    (2, "Diagonal Chase"),
    (3, "Gradient Wave"),
    (4, "Aurora"),
    (5, "Breathing Pulse"),
    (6, "Ambilight"),
    (7, "Warm White"),
]
MODE_NAMES = {
    1: "colorwave",
    2: "diagonal",
    3: "gradient",
    4: "aurora",
    5: "breathing",
    6: "ambilight",
    7: "warmwhite",
}


def run_ledctl(*args):
    try:
        return subprocess.run(
            [LEDCTL, *args], capture_output=True, text=True, timeout=3
        ).stdout.strip()
    except Exception as e:
        return f"ERR {e}"


class LEDMenuApp(rumps.App):
    def __init__(self):
        super().__init__("\U0001F4A1", quit_button=None)  # 💡

        self.mode_items = {}
        for num, name in MODES:
            item = rumps.MenuItem(f"{num}. {name}", callback=self.make_mode_callback(num))
            self.mode_items[num] = item
            self.menu.add(item)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Power On", callback=self.power_on))
        self.menu.add(rumps.MenuItem("Power Off", callback=self.power_off))

        self.menu.add(rumps.separator)
        self.status_item = rumps.MenuItem("Status: checking...")
        self.status_item.set_callback(None)  # informational only, not clickable
        self.menu.add(self.status_item)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=self.quit_app))

        self.refresh_status()

    def make_mode_callback(self, num):
        def callback(_sender):
            run_ledctl("mode", str(num))
            self.refresh_status()
        return callback

    def power_on(self, _sender):
        run_ledctl("on")
        self.refresh_status()

    def power_off(self, _sender):
        run_ledctl("off")
        self.refresh_status()

    def quit_app(self, _sender):
        rumps.quit_application()

    @rumps.timer(5)
    def refresh_status(self, _sender=None):
        raw = run_ledctl("status")
        try:
            data = json.loads(raw)
            power = data.get("power", "?")
            mode = data.get("mode", "?")
            self.status_item.title = f"Status: {mode} ({power})"
            for num, item in self.mode_items.items():
                item.state = 1 if MODE_NAMES.get(num) == mode else 0
        except (json.JSONDecodeError, ValueError):
            self.status_item.title = f"Status: unreachable ({raw})"
            for item in self.mode_items.values():
                item.state = 0


if __name__ == "__main__":
    LEDMenuApp().run()

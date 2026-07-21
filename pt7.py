#!/usr/bin/env python3
# PT-7 host for Windows and Linux; macOS uses init.lua (Hammerspoon) instead.
# Serves ui.html and injects real key events. Stdlib only on Windows;
# Linux needs: pip install evdev
#
# UNTESTED SCAFFOLD written on a Mac. Verify list lives in the vault plan
# "pt7-windows-linux-port" before trusting any of it.

import atexit
import json
import os
import platform
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
INTERFACE = os.environ.get("PT7_INTERFACE", "")
DIR = Path(__file__).resolve().parent

# "key" is a symbolic name each backend maps to its own codes.
# The talk key is right ctrl here: bind your dictation app's push-to-talk to it.
KEYS = [
    {"id": "talk",   "label": "talk",  "key": "right_ctrl", "role": "talk"},
    {"id": "up",     "label": "up",    "key": "up",    "repeats": True},
    {"id": "down",   "label": "down",  "key": "down",  "repeats": True},
    {"id": "tab",    "label": "tab",   "key": "tab"},
    {"id": "escape", "label": "esc",   "key": "esc",   "color": "red"},
    {"id": "enter",  "label": "enter", "key": "enter", "role": "primary", "color": "green"},
]
BY_ID = {k["id"]: k for k in KEYS}

REPEAT_DELAY = 0.30
REPEAT_INTERVAL = 0.05


class WindowsKeys:
    # Arrows and right ctrl are extended (E0) keys; SendInput needs the flag.
    VK = {"right_ctrl": 0xA3, "up": 0x26, "down": 0x28,
          "tab": 0x09, "esc": 0x1B, "enter": 0x0D}
    EXTENDED = {"right_ctrl", "up", "down"}
    repeats_in_host = True  # SendInput generates no typematic repeat

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT),
                            ("pad", ctypes.c_byte * 32)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self._ctypes = ctypes
        self._INPUT = INPUT
        self._KEYBDINPUT = KEYBDINPUT

    def send(self, name, down):
        ct = self._ctypes
        flags = 0 if down else 0x2                       # KEYEVENTF_KEYUP
        if name in self.EXTENDED:
            flags |= 0x1                                 # KEYEVENTF_EXTENDEDKEY
        inp = self._INPUT(type=1)                        # INPUT_KEYBOARD
        inp.ki = self._KEYBDINPUT(wVk=self.VK[name], wScan=0,
                                  dwFlags=flags, time=0, dwExtraInfo=None)
        ct.windll.user32.SendInput(1, ct.byref(inp), ct.sizeof(inp))


class LinuxKeys:
    # uinput is below X11/Wayland, so the same code works on both.
    # Needs write access to /dev/uinput: udev rule + "input" group, see plan.
    repeats_in_host = False  # the display server auto-repeats held evdev keys

    def __init__(self):
        from evdev import UInput, ecodes
        self._e = ecodes
        self.CODE = {"right_ctrl": ecodes.KEY_RIGHTCTRL, "up": ecodes.KEY_UP,
                     "down": ecodes.KEY_DOWN, "tab": ecodes.KEY_TAB,
                     "esc": ecodes.KEY_ESC, "enter": ecodes.KEY_ENTER}
        self._ui = UInput({ecodes.EV_KEY: list(self.CODE.values())}, name="PT-7")

    def send(self, name, down):
        self._ui.write(self._e.EV_KEY, self.CODE[name], 1 if down else 0)
        self._ui.syn()


BACKEND = WindowsKeys() if platform.system() == "Windows" else LinuxKeys()

_lock = threading.Lock()
_timers = {}


def press(key, down, is_repeat=False):
    BACKEND.send(key["key"], down)
    if not is_repeat:
        print(f"[PT-7] {key['id']} {'down' if down else 'up'}")


def stop_repeat(key):
    with _lock:
        t = _timers.pop(key["id"], None)
    if t:
        t.cancel()


def start_repeat(key):
    def fire():
        press(key, True, is_repeat=True)
        schedule(REPEAT_INTERVAL)

    def schedule(delay):
        with _lock:
            if key["id"] not in _timers:
                return
            _timers[key["id"]] = threading.Timer(delay, fire)
            _timers[key["id"]].daemon = True
            _timers[key["id"]].start()

    with _lock:
        if key["id"] in _timers:
            return
        _timers[key["id"]] = threading.Timer(REPEAT_DELAY, fire)
        _timers[key["id"]].daemon = True
        _timers[key["id"]].start()


def release_all():
    for key in KEYS:
        stop_repeat(key)
        press(key, False)


def token():
    base = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "pt7"
    f = base / "token"
    if f.exists():
        return f.read_text().strip()
    base.mkdir(parents=True, exist_ok=True)
    t = secrets.token_hex(4)
    f.write_text(t)
    return t


TOKEN = token()


def page():
    html = (DIR / "ui.html").read_text(encoding="utf-8")
    return html.replace("__KEYS__", json.dumps(KEYS), 1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status, body=b"", ctype="text/plain", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        prefix = "/" + TOKEN
        if not self.path.startswith(prefix):
            return self._reply(404, b"not found")
        sub = self.path[len(prefix):]
        if sub == "":
            return self._reply(302, extra={"Location": prefix + "/"})
        if sub == "/":
            return self._reply(200, page().encode(), "text/html; charset=utf-8")
        if sub == "/ping":
            return self._reply(200, b"ok")
        return self._reply(404, b"not found")

    def do_POST(self):
        prefix = "/" + TOKEN + "/"
        if not self.path.startswith(prefix):
            return self._reply(404, b"not found")
        parts = self.path[len(prefix):].split("/")
        if len(parts) == 2 and parts[0] in BY_ID and parts[1] in ("down", "up"):
            key = BY_ID[parts[0]]
            if parts[1] == "down":
                press(key, True)
                if key.get("repeats") and BACKEND.repeats_in_host:
                    start_repeat(key)
            else:
                stop_repeat(key)
                press(key, False)
            return self._reply(200, b"ok")
        return self._reply(404, b"not found")


if __name__ == "__main__":
    atexit.register(release_all)
    server = ThreadingHTTPServer((INTERFACE, PORT), Handler)
    host = platform.node().split(".")[0] or "localhost"
    print(f"[PT-7] deck at http://{host}:{PORT}/{TOKEN}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

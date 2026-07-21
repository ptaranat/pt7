#!/usr/bin/env -S uv run --script
# PT-7 host for Windows and Linux; macOS uses init.lua (Hammerspoon) instead.
# Serves ui.html and injects real key events. Run it with: uv run pt7.py
#
# /// script
# requires-python = ">=3.9"
# dependencies = ["evdev>=1.7 ; sys_platform == 'linux'"]
# ///

import atexit
import json
import os
import platform
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
INTERFACE = os.environ.get("PT7_INTERFACE", "")
DIR = Path(__file__).resolve().parent
ICON = DIR / "icon.png"

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

        user32 = ctypes.WinDLL("user32", use_last_error=True)

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

        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT

        self._ctypes = ctypes
        self._user32 = user32
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
        sent = self._user32.SendInput(1, ct.byref(inp), ct.sizeof(inp))
        if sent != 1:
            err = ct.get_last_error()
            print(f"[PT-7] SendInput blocked for {name} (error {err}); "
                  "an elevated window in front means the host needs elevation too")


UINPUT_HELP = """[PT-7] cannot open /dev/uinput: {err}

Give yourself access once, then log out and back in:

  echo 'KERNEL=="uinput", MODE="0660", GROUP="input"' | \\
    sudo tee /etc/udev/rules.d/99-pt7-uinput.rules
  sudo udevadm control --reload-rules && sudo udevadm trigger
  sudo usermod -aG input $USER

If the node is missing entirely: sudo modprobe uinput
"""


class LinuxKeys:
    # uinput is below X11/Wayland, so the same code works on both.
    repeats_in_host = False  # the display server auto-repeats held evdev keys

    def __init__(self):
        from evdev import UInput, ecodes
        self._e = ecodes
        self.CODE = {"right_ctrl": ecodes.KEY_RIGHTCTRL, "up": ecodes.KEY_UP,
                     "down": ecodes.KEY_DOWN, "tab": ecodes.KEY_TAB,
                     "esc": ecodes.KEY_ESC, "enter": ecodes.KEY_ENTER}
        try:
            self._ui = UInput({ecodes.EV_KEY: list(self.CODE.values())}, name="PT-7")
        except Exception as err:
            raise SystemExit(UINPUT_HELP.format(err=err))
        time.sleep(1)  # the compositor drops events sent before it adopts the device

    def send(self, name, down):
        self._ui.write(self._e.EV_KEY, self.CODE[name], 1 if down else 0)
        self._ui.syn()


BACKEND = WindowsKeys() if platform.system() == "Windows" else LinuxKeys()

# Every send happens under _lock, so a repeat timer that fires mid-release
# cannot slip a down event in after the up and leave the key stuck.
_lock = threading.Lock()
_timers = {}
_held = set()


def key_down(key):
    with _lock:
        _held.add(key["id"])
        BACKEND.send(key["key"], True)
        # A lost response makes the phone re-post "down"; without the _timers
        # check that duplicate would start a second repeat chain.
        if key.get("repeats") and BACKEND.repeats_in_host and key["id"] not in _timers:
            _schedule_repeat(key, REPEAT_DELAY)
    print(f"[PT-7] {key['id']} down")


def key_up(key):
    with _lock:
        _held.discard(key["id"])
        t = _timers.pop(key["id"], None)
        if t:
            t.cancel()
        BACKEND.send(key["key"], False)
    print(f"[PT-7] {key['id']} up")


def _schedule_repeat(key, delay):  # caller holds _lock
    def fire():
        with _lock:
            if key["id"] not in _held:
                _timers.pop(key["id"], None)
                return
            BACKEND.send(key["key"], True)
            _schedule_repeat(key, REPEAT_INTERVAL)

    t = threading.Timer(delay, fire)
    t.daemon = True
    _timers[key["id"]] = t
    t.start()


def release_all():
    for key in KEYS:
        key_up(key)


def token():
    base = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "pt7"
    f = base / "token"
    if f.exists():
        return f.read_text().strip()
    base.mkdir(parents=True, exist_ok=True)
    t = secrets.token_hex(4)
    f.write_text(t)
    f.chmod(0o600)
    return t


TOKEN = token()


def page():
    html = (DIR / "ui.html").read_text(encoding="utf-8")
    return html.replace("__KEYS__", json.dumps(KEYS), 1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: no TCP handshake per key press

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
        if sub == "/icon.png" and ICON.exists():
            return self._reply(200, ICON.read_bytes(), "image/png")
        return self._reply(404, b"not found")

    def do_POST(self):
        prefix = "/" + TOKEN + "/"
        if not self.path.startswith(prefix):
            return self._reply(404, b"not found")
        parts = self.path[len(prefix):].split("/")
        if len(parts) == 2 and parts[0] in BY_ID and parts[1] in ("down", "up"):
            key = BY_ID[parts[0]]
            if parts[1] == "down":
                key_down(key)
            else:
                key_up(key)
            return self._reply(200, b"ok")
        return self._reply(404, b"not found")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets, just picks the outbound route
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


if __name__ == "__main__":
    atexit.register(release_all)
    server = ThreadingHTTPServer((INTERFACE, PORT), Handler)
    host = platform.node().split(".")[0] or "localhost"
    print(f"[PT-7] deck at http://{host}:{PORT}/{TOKEN}/", flush=True)
    ip = INTERFACE or lan_ip()
    if ip:
        # Windows has no Bonjour, so the hostname above may not resolve from a phone.
        print(f"[PT-7] or by address  http://{ip}:{PORT}/{TOKEN}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

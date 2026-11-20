"""Windows: mouse y teclado vía SendInput (user32), con ctypes.

El mouse usa coordenadas absolutas normalizadas sobre el escritorio virtual
(todos los monitores), igual que el puntero absoluto de Linux: no depende
del escalado (DPI) de cada pantalla.
"""

import ctypes
import time
from ctypes import wintypes

from .keycodes import MODIFIERS, e, parse_combo

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_WHEEL = 0x0800
BUTTON_FLAGS = {e.BTN_LEFT: (0x0002, 0x0004), e.BTN_RIGHT: (0x0008, 0x0010), e.BTN_MIDDLE: (0x0020, 0x0040)}
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002

# Código de handctl → virtual-key de Windows.
VK: dict[int, int] = {getattr(e, f"KEY_{c}"): ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
VK.update({
    e.KEY_ESC: 0x1B, e.KEY_BACKSPACE: 0x08, e.KEY_TAB: 0x09, e.KEY_ENTER: 0x0D, e.KEY_SPACE: 0x20,
    e.KEY_MINUS: 0xBD, e.KEY_EQUAL: 0xBB, e.KEY_LEFTBRACE: 0xDB, e.KEY_RIGHTBRACE: 0xDD,
    e.KEY_SEMICOLON: 0xBA, e.KEY_APOSTROPHE: 0xDE, e.KEY_GRAVE: 0xC0, e.KEY_BACKSLASH: 0xDC,
    e.KEY_COMMA: 0xBC, e.KEY_DOT: 0xBE, e.KEY_SLASH: 0xBF, e.KEY_CAPSLOCK: 0x14,
    e.KEY_LEFTCTRL: 0xA2, e.KEY_RIGHTCTRL: 0xA3, e.KEY_LEFTSHIFT: 0xA0, e.KEY_RIGHTSHIFT: 0xA1,
    e.KEY_LEFTALT: 0xA4, e.KEY_RIGHTALT: 0xA5, e.KEY_LEFTMETA: 0x5B, e.KEY_RIGHTMETA: 0x5C,
    e.KEY_LEFT: 0x25, e.KEY_UP: 0x26, e.KEY_RIGHT: 0x27, e.KEY_DOWN: 0x28,
    e.KEY_HOME: 0x24, e.KEY_END: 0x23, e.KEY_PAGEUP: 0x21, e.KEY_PAGEDOWN: 0x22,
    e.KEY_INSERT: 0x2D, e.KEY_DELETE: 0x2E,
    e.KEY_MUTE: 0xAD, e.KEY_VOLUMEDOWN: 0xAE, e.KEY_VOLUMEUP: 0xAF,
    e.KEY_NEXTSONG: 0xB0, e.KEY_PREVIOUSSONG: 0xB1, e.KEY_PLAYPAUSE: 0xB3,
})
VK.update({getattr(e, f"KEY_F{i}"): 0x6F + i for i in range(1, 13)})
EXTENDED = {e.KEY_LEFT, e.KEY_UP, e.KEY_RIGHT, e.KEY_DOWN, e.KEY_HOME, e.KEY_END, e.KEY_PAGEUP,
            e.KEY_PAGEDOWN, e.KEY_INSERT, e.KEY_DELETE, e.KEY_RIGHTCTRL, e.KEY_RIGHTALT,
            e.KEY_LEFTMETA, e.KEY_RIGHTMETA}

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0))


def _key(code: int, up: bool) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if code in EXTENDED else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(VK[code], 0, flags, 0, 0))


class VirtualMouse:
    def __init__(self):
        self.pressed: set[int] = set()
        self._wheel_acc = 0.0

    def move(self, x: float, y: float) -> None:
        nx = round(min(max(x, 0.0), 1.0) * 65535)
        ny = round(min(max(y, 0.0), 1.0) * 65535)
        _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))

    def button(self, btn: int, down: bool) -> None:
        if down == (btn in self.pressed):
            return
        _send(_mouse(BUTTON_FLAGS[btn][0 if down else 1]))
        (self.pressed.add if down else self.pressed.discard)(btn)

    def click(self, btn: int) -> None:
        self.button(btn, True)
        self.button(btn, False)

    def scroll_hires(self, amount: float) -> None:
        """Windows acepta deltas de rueda menores a 120 (scroll suave)."""
        self._wheel_acc += amount
        delta = int(self._wheel_acc)
        if delta:
            self._wheel_acc -= delta
            _send(_mouse(MOUSEEVENTF_WHEEL, data=delta))

    def release_all(self) -> None:
        for btn in list(self.pressed):
            self.button(btn, False)

    def close(self) -> None:
        self.release_all()


class VirtualKeyboard:
    def __init__(self, keycodes: set[int]):
        missing = [c for c in set(keycodes) | set(MODIFIERS.values()) if c not in VK]
        if missing:
            raise ValueError(f"teclas sin equivalente en Windows: {missing}")

    def tap(self, code: int, modifiers: tuple[int, ...] = ()) -> None:
        seq = [_key(m, False) for m in modifiers] + [_key(code, False), _key(code, True)]
        seq += [_key(m, True) for m in reversed(modifiers)]
        _send(*seq)

    def combo(self, combo: str) -> None:
        mods, code = parse_combo(combo)
        self.tap(code, mods)

    def close(self) -> None:
        pass


def settle() -> None:
    time.sleep(0.05)

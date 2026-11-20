"""Códigos de tecla portables y atajos por sistema operativo.

Los códigos son los de Linux (linux/input-event-codes.h), usados como
identificadores en todo el programa; cada backend de entrada (vinput_*.py)
los traduce a lo que entiende su sistema. Así no hace falta `evdev` fuera
de Linux.
"""

import sys
from types import SimpleNamespace

IS_LINUX = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

_CODES = {
    "KEY_ESC": 1, "KEY_1": 2, "KEY_2": 3, "KEY_3": 4, "KEY_4": 5, "KEY_5": 6, "KEY_6": 7,
    "KEY_7": 8, "KEY_8": 9, "KEY_9": 10, "KEY_0": 11, "KEY_MINUS": 12, "KEY_EQUAL": 13,
    "KEY_BACKSPACE": 14, "KEY_TAB": 15, "KEY_Q": 16, "KEY_W": 17, "KEY_E": 18, "KEY_R": 19,
    "KEY_T": 20, "KEY_Y": 21, "KEY_U": 22, "KEY_I": 23, "KEY_O": 24, "KEY_P": 25,
    "KEY_LEFTBRACE": 26, "KEY_RIGHTBRACE": 27, "KEY_ENTER": 28, "KEY_LEFTCTRL": 29,
    "KEY_A": 30, "KEY_S": 31, "KEY_D": 32, "KEY_F": 33, "KEY_G": 34, "KEY_H": 35, "KEY_J": 36,
    "KEY_K": 37, "KEY_L": 38, "KEY_SEMICOLON": 39, "KEY_APOSTROPHE": 40, "KEY_GRAVE": 41,
    "KEY_LEFTSHIFT": 42, "KEY_BACKSLASH": 43, "KEY_Z": 44, "KEY_X": 45, "KEY_C": 46, "KEY_V": 47,
    "KEY_B": 48, "KEY_N": 49, "KEY_M": 50, "KEY_COMMA": 51, "KEY_DOT": 52, "KEY_SLASH": 53,
    "KEY_RIGHTSHIFT": 54, "KEY_LEFTALT": 56, "KEY_SPACE": 57, "KEY_CAPSLOCK": 58,
    "KEY_F1": 59, "KEY_F2": 60, "KEY_F3": 61, "KEY_F4": 62, "KEY_F5": 63, "KEY_F6": 64,
    "KEY_F7": 65, "KEY_F8": 66, "KEY_F9": 67, "KEY_F10": 68, "KEY_F11": 87, "KEY_F12": 88,
    "KEY_RIGHTCTRL": 97, "KEY_RIGHTALT": 100, "KEY_HOME": 102, "KEY_UP": 103, "KEY_PAGEUP": 104,
    "KEY_LEFT": 105, "KEY_RIGHT": 106, "KEY_END": 107, "KEY_DOWN": 108, "KEY_PAGEDOWN": 109,
    "KEY_INSERT": 110, "KEY_DELETE": 111, "KEY_MUTE": 113, "KEY_VOLUMEDOWN": 114,
    "KEY_VOLUMEUP": 115, "KEY_LEFTMETA": 125, "KEY_RIGHTMETA": 126, "KEY_NEXTSONG": 163,
    "KEY_PLAYPAUSE": 164, "KEY_PREVIOUSSONG": 165,
    "BTN_LEFT": 0x110, "BTN_RIGHT": 0x111, "BTN_MIDDLE": 0x112,
}
e = SimpleNamespace(**_CODES)

# Modificador "principal": Ctrl en Linux/Windows, ⌘ Cmd en Mac (copiar, pegar…).
MODIFIERS = {"ctrl": e.KEY_LEFTCTRL, "shift": e.KEY_LEFTSHIFT, "alt": e.KEY_LEFTALT,
             "option": e.KEY_LEFTALT, "meta": e.KEY_LEFTMETA, "super": e.KEY_LEFTMETA,
             "win": e.KEY_LEFTMETA, "cmd": e.KEY_LEFTMETA,
             "primary": e.KEY_LEFTMETA if IS_MAC else e.KEY_LEFTCTRL}
PRIMARY = MODIFIERS["primary"]
ALIASES = {"esc": "ESC", "escape": "ESC", "return": "ENTER", "del": "DELETE",
           "pgup": "PAGEUP", "pgdn": "PAGEDOWN", ".": "DOT", ",": "COMMA", "-": "MINUS"}


def parse_combo(combo: str) -> tuple[tuple[int, ...], int]:
    """"primary+shift+t" → ((KEY_LEFTCTRL, KEY_LEFTSHIFT), KEY_T) (en Mac, ⌘ en vez de Ctrl)."""
    *mods, key = [p.strip().lower() for p in combo.split("+")]
    name = ALIASES.get(key, key.upper())
    code = _CODES.get(f"KEY_{name}")
    if code is None or any(m not in MODIFIERS for m in mods):
        raise ValueError(f"Atajo inválido: {combo!r}")
    return tuple(MODIFIERS[m] for m in mods), code


# Atajos del sistema para los gestos de deslizar y el lanzador.
if IS_MAC:
    SHORTCUTS = {"overview": "ctrl+up", "desk_left": "ctrl+left", "desk_right": "ctrl+right",
                 "show_desktop": "f11"}
elif IS_WINDOWS:
    SHORTCUTS = {"overview": "win+tab", "desk_left": "ctrl+win+left", "desk_right": "ctrl+win+right",
                 "show_desktop": "win+d"}
else:  # KDE Plasma (en GNOME: overview = "super", escritorio = "super+pgup/pgdn")
    SHORTCUTS = {"overview": "meta+w", "desk_left": "ctrl+meta+left", "desk_right": "ctrl+meta+right",
                 "show_desktop": "meta+d"}

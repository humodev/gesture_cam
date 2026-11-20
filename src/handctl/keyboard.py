"""Teclado en pantalla dibujado sobre la vista previa (layout US, el del sistema).

Las teclas se envían al teclado virtual, así que llegan a la ventana que
tenga el foco, no a la vista previa.
"""

from dataclasses import dataclass

import cv2
from .keycodes import IS_MAC, PRIMARY, e


@dataclass
class Key:
    label: str
    code: int | None = None
    shift_label: str | None = None
    width: float = 1.0
    kind: str = "char"  # char | shift | ctrl | mode
    repeat: bool = False


def _chars(chars: str, shifted: str, codes: list[int]) -> list[Key]:
    return [Key(c, k, s) for c, s, k in zip(chars, shifted, codes)]


ROWS: list[list[Key]] = [
    _chars("1234567890-", "!@#$%^&*()_",
           [e.KEY_1, e.KEY_2, e.KEY_3, e.KEY_4, e.KEY_5, e.KEY_6, e.KEY_7,
            e.KEY_8, e.KEY_9, e.KEY_0, e.KEY_MINUS])
    + [Key("<-", e.KEY_BACKSPACE, width=1.5, repeat=True)],
    [Key("Tab", e.KEY_TAB, width=1.5)]
    + _chars("qwertyuiop'", "QWERTYUIOP\"",
             [e.KEY_Q, e.KEY_W, e.KEY_E, e.KEY_R, e.KEY_T, e.KEY_Y, e.KEY_U,
              e.KEY_I, e.KEY_O, e.KEY_P, e.KEY_APOSTROPHE]),
    _chars("asdfghjkl;", "ASDFGHJKL:",
           [e.KEY_A, e.KEY_S, e.KEY_D, e.KEY_F, e.KEY_G, e.KEY_H, e.KEY_J,
            e.KEY_K, e.KEY_L, e.KEY_SEMICOLON])
    + [Key("Enter", e.KEY_ENTER, width=2.5)],
    [Key("Shift", kind="shift", width=2.5)]
    + _chars("zxcvbnm,./", "ZXCVBNM<>?",
             [e.KEY_Z, e.KEY_X, e.KEY_C, e.KEY_V, e.KEY_B, e.KEY_N, e.KEY_M,
              e.KEY_COMMA, e.KEY_DOT, e.KEY_SLASH]),
    [Key("Mouse", kind="mode", width=2.0), Key("Cmd" if IS_MAC else "Ctrl", kind="ctrl", width=1.5),
     Key("Space", e.KEY_SPACE, width=5.0),
     Key("<", e.KEY_LEFT, repeat=True), Key("^", e.KEY_UP, repeat=True),
     Key("v", e.KEY_DOWN, repeat=True), Key(">", e.KEY_RIGHT, repeat=True)],
]

KEYCODES = {k.code for row in ROWS for k in row if k.code is not None} | {
    e.KEY_LEFTSHIFT, PRIMARY}

SHIFT_OFF, SHIFT_ONCE, SHIFT_LOCK = 0, 1, 2


class OnScreenKeyboard:
    def __init__(self):
        self.shift = SHIFT_OFF
        self.ctrl = False
        self.hover: dict[str, Key | None] = {}  # tecla bajo el cursor de cada mano
        self.flash: Key | None = None
        self.flash_until = 0.0
        self._rects: list[tuple[Key, tuple[int, int, int, int]]] = []

    def layout(self, x0: int, y0: int, w: int, h: int) -> None:
        units = max(sum(k.width for k in row) for row in ROWS)
        kw, kh = w / units, h / len(ROWS)
        self._rects = []
        for r, row in enumerate(ROWS):
            x = x0 + (w - sum(k.width for k in row) * kw) / 2
            for k in row:
                kx2 = x + k.width * kw
                self._rects.append((k, (int(x), int(y0 + r * kh), int(kx2), int(y0 + (r + 1) * kh))))
                x = kx2

    def key_at(self, px: float, py: float, current: Key | None = None,
               stickiness: float = 0.0) -> Key | None:
        """Tecla bajo (px, py). Si `current` sigue cerca (dentro de su rectángulo
        agrandado por `stickiness`), se mantiene: el temblor no hace saltar el hover."""
        for k, (x1, y1, x2, y2) in self._rects:
            if k is current:
                mx, my = (x2 - x1) * stickiness, (y2 - y1) * stickiness
                if x1 - mx <= px < x2 + mx and y1 - my <= py < y2 + my:
                    return k
        for k, (x1, y1, x2, y2) in self._rects:
            if x1 <= px < x2 and y1 <= py < y2:
                return k
        return None

    def press(self, key: Key, kbd, now: float) -> str | None:
        """Ejecuta la tecla. Devuelve "mode" si hay que volver a modo mouse."""
        self.flash, self.flash_until = key, now + 0.15
        if key.kind == "mode":
            return "mode"
        if key.kind == "shift":
            self.shift = (self.shift + 1) % 3
            return None
        if key.kind == "ctrl":
            self.ctrl = not self.ctrl
            return None
        mods = []
        if self.ctrl:
            mods.append(PRIMARY)  # Ctrl (⌘ en Mac): Ctrl+C del teclado = copiar en todos
        if self.shift and key.shift_label:
            mods.append(e.KEY_LEFTSHIFT)
        kbd.tap(key.code, tuple(mods))
        self.ctrl = False
        if self.shift == SHIFT_ONCE and key.shift_label:
            self.shift = SHIFT_OFF
        return None

    def keys(self) -> list[tuple[Key, tuple[int, int, int, int]]]:
        return list(self._rects)

    def style(self, k: Key, now: float) -> str:
        """"flash" (recién pulsada), "hover", "active" (modificador activo) o "normal"."""
        if k is self.flash and now < self.flash_until:
            return "flash"
        if any(k is h for h in list(self.hover.values())):
            return "hover"
        if (k.kind == "shift" and self.shift) or (k.kind == "ctrl" and self.ctrl):
            return "active"
        return "normal"

    def label(self, k: Key) -> str:
        if k.kind == "shift" and self.shift == SHIFT_LOCK:
            return "CAPS"
        if self.shift and k.shift_label:
            return k.shift_label
        return k.label

    def draw(self, img, now: float) -> None:
        colors = {"flash": (60, 200, 60), "hover": (200, 140, 30),
                  "active": (40, 120, 200), "normal": (40, 40, 40)}
        overlay = img.copy()
        for k, (x1, y1, x2, y2) in self._rects:
            cv2.rectangle(overlay, (x1 + 2, y1 + 2), (x2 - 2, y2 - 2), colors[self.style(k, now)], -1)
        cv2.addWeighted(overlay, 0.75, img, 0.25, 0, dst=img)

        for k, (x1, y1, x2, y2) in self._rects:
            label = self.label(k)
            scale = 0.7 if len(label) == 1 else 0.5
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
            cv2.putText(img, label, ((x1 + x2 - tw) // 2, (y1 + y2 + th) // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)

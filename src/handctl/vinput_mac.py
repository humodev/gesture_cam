"""macOS: mouse y teclado con pynput (eventos Quartz).

Requiere dar permiso de Accesibilidad a la terminal (o a Python) en
Ajustes del Sistema → Privacidad y seguridad → Accesibilidad.

El mouse va en coordenadas absolutas (puntos) sobre el rectángulo que cubre
todos los monitores. pynput manda eventos de "arrastre" cuando hay un botón
apretado, así que arrastrar funciona.
"""

import time

from .keycodes import MODIFIERS, e, parse_combo

# Teclas que pynput escribe como carácter (respeta el layout del sistema).
CHARS: dict[int, str] = {getattr(e, f"KEY_{c}"): c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
CHARS.update({e.KEY_MINUS: "-", e.KEY_EQUAL: "=", e.KEY_LEFTBRACE: "[", e.KEY_RIGHTBRACE: "]",
              e.KEY_SEMICOLON: ";", e.KEY_APOSTROPHE: "'", e.KEY_GRAVE: "`", e.KEY_BACKSLASH: "\\",
              e.KEY_COMMA: ",", e.KEY_DOT: ".", e.KEY_SLASH: "/"})
# Teclas especiales → nombre en pynput.keyboard.Key.
SPECIAL: dict[int, str] = {
    e.KEY_ESC: "esc", e.KEY_BACKSPACE: "backspace", e.KEY_TAB: "tab", e.KEY_ENTER: "enter",
    e.KEY_SPACE: "space", e.KEY_CAPSLOCK: "caps_lock",
    e.KEY_LEFTCTRL: "ctrl", e.KEY_RIGHTCTRL: "ctrl_r", e.KEY_LEFTSHIFT: "shift", e.KEY_RIGHTSHIFT: "shift_r",
    e.KEY_LEFTALT: "alt", e.KEY_RIGHTALT: "alt_r", e.KEY_LEFTMETA: "cmd", e.KEY_RIGHTMETA: "cmd_r",
    e.KEY_LEFT: "left", e.KEY_UP: "up", e.KEY_RIGHT: "right", e.KEY_DOWN: "down",
    e.KEY_HOME: "home", e.KEY_END: "end", e.KEY_PAGEUP: "page_up", e.KEY_PAGEDOWN: "page_down",
    e.KEY_DELETE: "delete",
    e.KEY_MUTE: "media_volume_mute", e.KEY_VOLUMEDOWN: "media_volume_down",
    e.KEY_VOLUMEUP: "media_volume_up", e.KEY_PLAYPAUSE: "media_play_pause",
    e.KEY_NEXTSONG: "media_next", e.KEY_PREVIOUSSONG: "media_previous",
}
SPECIAL.update({getattr(e, f"KEY_F{i}"): f"f{i}" for i in range(1, 13)})
BUTTONS = {e.BTN_LEFT: "left", e.BTN_RIGHT: "right", e.BTN_MIDDLE: "middle"}
WHEEL_STEP = 120  # unidades hi-res por línea de scroll


def desktop_bounds() -> tuple[float, float, float, float]:
    """(x, y, ancho, alto) en puntos del rectángulo que cubre todos los monitores."""
    import Quartz
    err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    rects = [Quartz.CGDisplayBounds(d) for d in ids[:count]] or [Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())]
    x0 = min(r.origin.x for r in rects)
    y0 = min(r.origin.y for r in rects)
    x1 = max(r.origin.x + r.size.width for r in rects)
    y1 = max(r.origin.y + r.size.height for r in rects)
    return x0, y0, x1 - x0, y1 - y0


class VirtualMouse:
    def __init__(self):
        from pynput.mouse import Button, Controller
        self._m, self._buttons = Controller(), {k: getattr(Button, v) for k, v in BUTTONS.items()}
        self.bounds = desktop_bounds()
        self.pressed: set[int] = set()
        self._wheel_acc = 0.0

    def move(self, x: float, y: float) -> None:
        bx, by, bw, bh = self.bounds
        self._m.position = (bx + min(max(x, 0.0), 1.0) * (bw - 1), by + min(max(y, 0.0), 1.0) * (bh - 1))

    def button(self, btn: int, down: bool) -> None:
        if down == (btn in self.pressed):
            return
        (self._m.press if down else self._m.release)(self._buttons[btn])
        (self.pressed.add if down else self.pressed.discard)(btn)

    def click(self, btn: int) -> None:
        self.button(btn, True)
        self.button(btn, False)

    def scroll_hires(self, amount: float) -> None:
        self._wheel_acc += amount
        steps = int(self._wheel_acc / WHEEL_STEP)
        if steps:
            self._wheel_acc -= steps * WHEEL_STEP
            self._m.scroll(0, steps)

    def release_all(self) -> None:
        for btn in list(self.pressed):
            self.button(btn, False)

    def close(self) -> None:
        self.release_all()


class VirtualKeyboard:
    def __init__(self, keycodes: set[int]):
        from pynput.keyboard import Controller, Key, KeyCode
        missing = [c for c in set(keycodes) | set(MODIFIERS.values()) if c not in CHARS and c not in SPECIAL]
        if missing:
            raise ValueError(f"teclas sin equivalente en macOS: {missing}")
        self._k = Controller()
        self._map = {c: KeyCode.from_char(ch) for c, ch in CHARS.items()}
        self._map.update({c: getattr(Key, name) for c, name in SPECIAL.items() if hasattr(Key, name)})

    def tap(self, code: int, modifiers: tuple[int, ...] = ()) -> None:
        for m in modifiers:
            self._k.press(self._map[m])
        try:
            self._k.press(self._map[code])
            self._k.release(self._map[code])
        finally:
            for m in reversed(modifiers):
                self._k.release(self._map[m])

    def combo(self, combo: str) -> None:
        mods, code = parse_combo(combo)
        self.tap(code, mods)

    def close(self) -> None:
        pass


def settle() -> None:
    time.sleep(0.05)

"""Mouse y teclado virtuales vía /dev/uinput. Funcionan en Wayland y X11
porque el compositor los ve como dispositivos de hardware reales."""

import time

from evdev import AbsInfo, UInput
from evdev import ecodes as e

ABS_MAX = 65535
WHEEL_DETENT = 120  # unidades hi-res por "clic" de rueda (convención del kernel)

MODIFIERS = {"ctrl": e.KEY_LEFTCTRL, "shift": e.KEY_LEFTSHIFT,
             "alt": e.KEY_LEFTALT, "meta": e.KEY_LEFTMETA, "super": e.KEY_LEFTMETA}


def parse_combo(combo: str) -> tuple[tuple[int, ...], int]:
    """"ctrl+meta+left" → ((KEY_LEFTCTRL, KEY_LEFTMETA), KEY_LEFT)."""
    *mods, key = [p.strip().lower() for p in combo.split("+")]
    code = getattr(e, f"KEY_{key.upper()}", None)
    if code is None or any(m not in MODIFIERS for m in mods):
        raise ValueError(f"Atajo inválido: {combo!r}")
    return tuple(MODIFIERS[m] for m in mods), code


class VirtualMouse:
    """Puntero absoluto (como una tableta de VM): la posición se mapea directo
    a la pantalla, sin la aceleración del sistema."""

    def __init__(self):
        abs_axis = AbsInfo(value=0, min=0, max=ABS_MAX, fuzz=0, flat=0, resolution=0)
        self.ui = UInput(
            {
                e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                e.EV_ABS: [(e.ABS_X, abs_axis), (e.ABS_Y, abs_axis)],
                e.EV_REL: [e.REL_WHEEL, e.REL_WHEEL_HI_RES],
            },
            name="handctl-mouse",
        )
        self.pressed: set[int] = set()
        self._wheel_acc = 0.0  # hi-res pendiente
        self._detent_acc = 0   # hi-res enviado que todavía no completa un paso

    def move(self, x: float, y: float) -> None:
        """x, y en [0, 1] relativos al escritorio completo."""
        self.ui.write(e.EV_ABS, e.ABS_X, round(min(max(x, 0.0), 1.0) * ABS_MAX))
        self.ui.write(e.EV_ABS, e.ABS_Y, round(min(max(y, 0.0), 1.0) * ABS_MAX))
        self.ui.syn()

    def button(self, btn: int, down: bool) -> None:
        if down == (btn in self.pressed):
            return
        self.ui.write(e.EV_KEY, btn, int(down))
        self.ui.syn()
        (self.pressed.add if down else self.pressed.discard)(btn)

    def click(self, btn: int) -> None:
        self.button(btn, True)
        self.button(btn, False)

    def scroll_hires(self, amount: float) -> None:
        """Scroll suave: `amount` en unidades hi-res (positivo = arriba)."""
        self._wheel_acc += amount
        hires = int(self._wheel_acc)
        if not hires:
            return
        self._wheel_acc -= hires
        self.ui.write(e.EV_REL, e.REL_WHEEL_HI_RES, hires)
        # Las apps sin soporte hi-res usan REL_WHEEL: un paso cada 120 unidades.
        self._detent_acc += hires
        detents = int(self._detent_acc / WHEEL_DETENT)
        if detents:
            self._detent_acc -= detents * WHEEL_DETENT
            self.ui.write(e.EV_REL, e.REL_WHEEL, detents)
        self.ui.syn()

    def release_all(self) -> None:
        for btn in list(self.pressed):
            self.button(btn, False)

    def close(self) -> None:
        self.release_all()
        self.ui.close()


class VirtualKeyboard:
    def __init__(self, keycodes: set[int]):
        all_codes = set(keycodes) | set(MODIFIERS.values())
        self.ui = UInput({e.EV_KEY: sorted(all_codes)}, name="handctl-keyboard")

    def tap(self, code: int, modifiers: tuple[int, ...] = ()) -> None:
        for m in modifiers:
            self.ui.write(e.EV_KEY, m, 1)
        self.ui.write(e.EV_KEY, code, 1)
        self.ui.syn()
        self.ui.write(e.EV_KEY, code, 0)
        for m in reversed(modifiers):
            self.ui.write(e.EV_KEY, m, 0)
        self.ui.syn()

    def combo(self, combo: str) -> None:
        mods, code = parse_combo(combo)
        self.tap(code, mods)

    def close(self) -> None:
        self.ui.close()


def settle() -> None:
    """El compositor tarda un instante en registrar dispositivos nuevos."""
    time.sleep(0.3)

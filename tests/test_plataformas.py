"""Backends de entrada de cada sistema operativo, probados sin mandar eventos
reales (se interceptan las llamadas a la API del sistema). Corre en Linux,
Windows y Mac.

    uv run python tests/test_plataformas.py
"""

import ctypes
import sys
from types import SimpleNamespace as NS

from handctl import vinput_mac, vinput_windows
from handctl.config import Config
from handctl.keyboard import KEYCODES
from handctl.keycodes import IS_WINDOWS, MODIFIERS, SHORTCUTS, e, parse_combo
from handctl.launcher import CATALOG, launcher_combos


def used_codes() -> set[int]:
    """Todas las teclas que el programa puede llegar a mandar."""
    cfg = Config()
    combos = [cfg.swipe_left, cfg.swipe_right, cfg.swipe_up, cfg.swipe_down, cfg.voice_paste,
              *SHORTCUTS.values(), *launcher_combos(cfg),
              *(c[3] for c in CATALOG.values() if c[2] == "combo")]
    codes = set(KEYCODES) | set(MODIFIERS.values())
    for combo in combos:
        mods, key = parse_combo(combo)
        codes |= {key, *mods}
    return codes


def test_parse_combo():
    assert parse_combo("ctrl+shift+t") == ((e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT), e.KEY_T)
    assert parse_combo("esc") == ((), e.KEY_ESC)
    primary = e.KEY_LEFTMETA if sys.platform == "darwin" else e.KEY_LEFTCTRL
    assert parse_combo("primary+c") == ((primary,), e.KEY_C)
    for bad in ("ctrl+", "hyper+c", "ctrl+noexiste"):
        try:
            parse_combo(bad)
        except ValueError:
            continue
        raise AssertionError(f"debió fallar: {bad}")


def test_windows_cubre_todas_las_teclas():
    missing = used_codes() - set(vinput_windows.VK)
    assert not missing, missing


def test_mac_cubre_todas_las_teclas():
    missing = used_codes() - set(vinput_mac.CHARS) - set(vinput_mac.SPECIAL)
    assert not missing, missing


def test_windows_eventos():
    sent = []
    vinput_windows._send = lambda *inputs: sent.extend(inputs)
    m = vinput_windows.VirtualMouse()
    m.move(0.5, 1.0)
    mi = sent[-1].mi
    assert sent[-1].type == vinput_windows.INPUT_MOUSE
    assert (mi.dx, mi.dy) == (32768, 65535)
    assert mi.dwFlags == 0x0001 | 0x8000 | 0x4000  # MOVE | ABSOLUTE | VIRTUALDESK
    sent.clear()
    m.button(e.BTN_LEFT, True)
    m.button(e.BTN_LEFT, True)    # repetido: no manda nada
    m.release_all()
    assert [i.mi.dwFlags for i in sent] == [0x0002, 0x0004]
    sent.clear()
    m.scroll_hires(60.4)
    m.scroll_hires(-200.0)
    deltas = [ctypes.c_int32(i.mi.mouseData).value for i in sent]
    assert deltas == [60, -199], deltas  # el resto fraccionario se acumula
    sent.clear()
    k = vinput_windows.VirtualKeyboard(KEYCODES)
    k.combo("ctrl+shift+left")
    seq = [(i.ki.wVk, bool(i.ki.dwFlags & 2), bool(i.ki.dwFlags & 1)) for i in sent]
    assert seq == [(0xA2, False, False), (0xA0, False, False), (0x25, False, True),
                   (0x25, True, True), (0xA0, True, False), (0xA2, True, False)], seq
    if IS_WINDOWS:  # el tamaño de INPUT tiene que coincidir con el de Windows
        assert ctypes.sizeof(vinput_windows.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_mac_eventos():
    log = []

    class FakeMouse:
        position = (0, 0)
        press = lambda self, b: log.append(("press", b))
        release = lambda self, b: log.append(("release", b))
        scroll = lambda self, dx, dy: log.append(("scroll", dy))

    m = vinput_mac.VirtualMouse.__new__(vinput_mac.VirtualMouse)
    m._m, m._buttons, m.bounds = FakeMouse(), {e.BTN_LEFT: "L", e.BTN_RIGHT: "R"}, (-1080, 0, 3000, 1920)
    m.pressed, m._wheel_acc = set(), 0.0
    m.move(0.0, 0.0)
    assert m._m.position == (-1080, 0)
    m.move(1.0, 0.5)
    assert m._m.position == (-1080 + 2999, 959.5)
    m.click(e.BTN_RIGHT)
    m.scroll_hires(130)
    m.scroll_hires(115)
    assert log == [("press", "R"), ("release", "R"), ("scroll", 1), ("scroll", 1)], log


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as ex:
                failed += 1
                print(f"FALLA {name}: {ex!r}")
    sys.exit(1 if failed else 0)

"""Instrumentos abiertos desde el host real: menú → Apps → instrumento →
tocar → dibujar con el overlay → cerrar con puño. Sin audio real (el
stream no se abre) ni ventana visible (Qt offscreen).

    uv run python tests/test_instrumentos.py
"""

import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
from PyQt6.QtWidgets import QApplication

import handctl.audio as audio

audio.Engine.start = lambda self: None  # no abrir la salida de audio

from handctl.apps import load_apps  # noqa: E402
from handctl.overlay import Overlay  # noqa: E402
from test_gestos import Sim, hand, index_of  # noqa: E402

qt = QApplication.instance() or QApplication([])


def render_audio(seconds: float) -> float:
    eng = audio.get_engine()
    out = np.zeros((audio.BLOCK, 2), np.float32)
    peak = 0.0
    for _ in range(int(seconds * audio.SR / audio.BLOCK)):
        eng._callback(out, audio.BLOCK, None, None)
        peak = max(peak, float(np.abs(out).max()))
    return peak


def open_instrument(title: str) -> Sim:
    s = Sim()
    s.ctl.set_apps(load_apps())
    s.engage()
    s.open_menu()
    s.choose(index_of(s, "apps"))
    # El submenú se centra donde quedó la mano (ya quieta): elegir desde ahí.
    ax, ay = s.ctl.launcher.anchor
    aspect = s.cfg.cam_width / s.cfg.cam_height
    palm_dx, palm_dy = np.mean([hand(0, 0)[i].x for i in (0, 5, 9, 17)]), np.mean([hand(0, 0)[i].y for i in (0, 5, 9, 17)])
    s.choose(index_of(s, title), x=ax / aspect - palm_dx, y=ay - palm_dy)
    assert s.ctl.mode == "app" and s.ctl.app.title == title, (s.ctl.mode, s.ctl.app)
    return s


def paint_with_overlay(s: Sim) -> None:
    """Pinta un frame con el Overlay real (camino _draw_app → app.paint)."""
    overlay = Overlay(s.cfg, qt.primaryScreen())
    overlay.display.attach(s.ctl)
    overlay.grab()
    assert s.ctl.app is not None, s.ctl.app_error


def close_with_fist(s: Sim) -> None:
    s.hold(0.3, ("right", hand(.4, .4)))
    s.hold(0.8, ("right", hand(.4, .4, "fist")))
    assert s.ctl.app is None and s.ctl.mode == "mouse", s.ctl.mode
    render_audio(0.05)
    assert not any(isinstance(v, (audio.ThereminVoice, audio.ArpVoice)) for v in audio.get_engine().voices)


def test_theremin():
    s = open_instrument("Theremín")
    s.hold(0.5, ("right", hand(.4, .3)))
    assert render_audio(0.3) > 0.01
    paint_with_overlay(s)
    close_with_fist(s)


def test_acordes():
    s = open_instrument("Acordes")
    s.hold(0.4, ("right", hand(.35, .2)))
    s.hold(0.3, ("right", hand(.35, .2, "index")))
    assert render_audio(0.3) > 0.01
    paint_with_overlay(s)
    close_with_fist(s)


def test_sinte():
    s = open_instrument("Sinte XY")
    s.hold(0.4, ("right", hand(.5, .3)))
    s.hold(0.5, ("right", hand(.5, .3, "index")))
    assert s.ctl.app.arp.gate and render_audio(0.3) > 0.01
    paint_with_overlay(s)
    close_with_fist(s)


def test_cuerdas():
    s = open_instrument("Cuerdas en el aire")
    app = s.ctl.app
    first = app.strings[0]
    # Bajar la punta del índice a través de la primera cuerda (de arriba hacia abajo).
    (rx0, rx1), (ry0, ry1) = s.cfg.region_x, s.cfg.region_y
    mid_x = (first.p1.x() + first.p2.x()) / 2
    cam_x = rx0 + mid_x / 1920 * (rx1 - rx0) + .04
    for sy in np.linspace(first.p1.y() - 150, first.p1.y() + 150, 10):
        cam_y = ry0 + sy / 1080 * (ry1 - ry0) + .05
        s.frames(1, ("right", hand(cam_x, cam_y)))
    assert first.pluck_amp > 0, "no se tocó la cuerda"
    assert render_audio(0.3) > 0.01
    paint_with_overlay(s)
    close_with_fist(s)


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

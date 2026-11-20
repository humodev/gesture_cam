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


def strike(s, cam_x: float, cam_y0: float, cam_y1: float, frames: int = 4) -> None:
    """Golpe rápido hacia abajo que frena en cam_y1 (y queda quieto)."""
    for y in np.linspace(cam_y0, cam_y1, frames):
        s.frames(1, ("right", hand(cam_x, y)))
    s.hold(0.2, ("right", hand(cam_x, cam_y1)))


def test_octapad():
    s = open_instrument("Octapad")
    app = s.ctl.app
    s.hold(0.3, ("right", hand(.3, .25)))
    hits = []
    app.hit = (lambda orig: lambda pad, force, side, now: (hits.append((pad, round(force, 2))), orig(pad, force, side, now)))(app.hit)
    # Mano que baja despacio: no suena.
    for y in np.linspace(.25, .45, 30):
        s.frames(1, ("right", hand(.3, y)))
    assert not hits, hits
    strike(s, .3, .25, .45)   # golpe hacia la fila de abajo
    strike(s, .3, .12, .30)   # otro, hacia la fila de arriba
    assert len(hits) == 2, hits
    # Aparece la otra mano y se intercambian las etiquetas: no debe sonar nada.
    s.frames(3, ("right", hand(.3, .45)), ("left", hand(.6, .2)))
    s.hold(0.3, ("right", hand(.3, .45)))
    assert len(hits) == 2, hits
    rows = [app.pads[p].center().y() < app.pads[4].top() for p, _ in hits]
    assert rows == [False, True], (hits, rows)
    assert render_audio(0.3) > 0.01
    # Cambiar de kit con la mano quieta sobre su botón.
    btn = app.kit_buttons["Electrónica"].center()
    (rx0, rx1), (ry0, ry1) = s.cfg.region_x, s.cfg.region_y
    palm = np.mean([[hand(0, 0)[i].x, hand(0, 0)[i].y] for i in (0, 5, 9, 17)], axis=0)
    cx = rx0 + btn.x() / 1920 * (rx1 - rx0) - palm[0]
    cy = ry0 + btn.y() / 1080 * (ry1 - ry0) - palm[1]
    s.hold(1.0, ("right", hand(cx, cy)))
    assert app.kit == "Electrónica", app.kit
    paint_with_overlay(s)
    close_with_fist(s)


def test_estudio_looper():
    s = open_instrument("Estudio (looper)")
    app, lp = s.ctl.app, s.ctl.app.looper
    lp.bpm, lp.bars = 120, 1          # bucle de 2 s
    app._press("rec")
    assert lp.state == "cuenta"
    render_audio(2.05)
    assert lp.state == "grabando", lp.state
    s.hold(0.3, ("right", hand(.3, .25)))
    strike(s, .3, .25, .45)           # tocar el octapad mientras graba
    render_audio(2.0)
    assert len(lp.layers) == 1 and lp.state == "sonando", (len(lp.layers), lp.state)
    assert float(np.abs(lp.layers[0]).max()) > 0.01, "la capa quedó muda"
    app._press("inst:theremin")
    assert type(app.inst).__name__ == "Theremin"
    paint_with_overlay(s)
    close_with_fist(s)
    lp.clear()


def test_pizarron():
    s = open_instrument("Pizarrón")
    app = s.ctl.app
    app.strokes.clear()
    point = lambda x, y: ("right", hand(x, y, "point"))
    s.hold(0.3, ("right", hand(.3, .3)))
    for x in np.linspace(.3, .5, 20):      # señalando: dibuja
        s.frames(1, point(x, .3))
    s.hold(0.3, ("right", hand(.5, .3)))  # mano abierta: levanta el lápiz
    for x in np.linspace(.5, .3, 10):      # moverse con la mano abierta no dibuja
        s.frames(1, ("right", hand(x, .35)))
    assert len(app.strokes) == 1 and len(app.strokes[0]["points"]) > 10, [len(t["points"]) for t in app.strokes]
    app._press("tool:láser", s.t)
    for x in np.linspace(.3, .5, 10):
        s.frames(1, point(x, .4))
    assert app.laser and len(app.strokes) == 1
    app._press("undo", s.t)
    assert not app.strokes
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

"""Pruebas de la lógica de gestos con manos SINTÉTICAS (sin cámara ni uinput).

    uv run python tests/test_gestos.py

Cada prueba arma un Controller con dispositivos falsos que anotan lo que
recibirían y le pasa frames a 30 fps con landmarks inventados.
"""

import math
import sys
from types import SimpleNamespace as NS

from handctl.apps import APPS, GestureApp
from handctl.config import Config
from handctl.controller import Controller

FPS = 30
ASPECT = 1280 / 720
# Mano abierta relativa a (ox, oy) en coords normalizadas de cámara.
BASE = [(0, .2), (-.06, .15), (-.09, .1), (-.11, .06), (-.12, .02), (-.04, .08), (-.04, .02),
        (-.04, -.02), (-.04, -.05), (0, .08), (0, .01), (0, -.03), (0, -.07), (.03, .08),
        (.03, .02), (.03, -.02), (.03, -.05), (.06, .09), (.06, .05), (.06, .02), (.06, 0)]
TIPS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}


def hand(ox: float, oy: float, pose: str = "open"):
    pts = list(BASE)
    if pose in TIPS:  # pellizco: el pulgar toca la punta del dedo
        pts[4] = pts[TIPS[pose]]
    elif pose in ("fist", "thumbs_up"):
        for i, p in zip((8, 12, 16, 20), ((-.04, .06), (0, .06), (.03, .07), (.06, .09))):
            pts[i] = p
        pts[4] = (-.02, .05) if pose == "fist" else (-.08, -.04)
    elif pose == "point":  # señalar: índice estirado, el resto cerrado
        for i, p in zip((12, 16, 20), ((0, .06), (.03, .07), (.06, .09))):
            pts[i] = p
        pts[4] = (-.02, .05)
    elif pose == "v":
        pts[16], pts[20] = (.03, .07), (.06, .09)
    return [NS(x=ox + x, y=oy + y, z=0.0) for x, y in pts]


def result(*hands):
    """hands: (lado, landmarks)."""
    return NS(hand_landmarks=[h for _, h in hands],
              handedness=[[NS(category_name=s.capitalize())] for s, _ in hands])


class Rec:
    """Dispositivo falso: anota las llamadas."""

    def __init__(self):
        self.calls, self.pressed = [], set()

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, *a))

    def names(self, name):
        return [c[1:] for c in self.calls if c[0] == name]

    def presses(self):
        """Botones apretados (soltar uno que no estaba apretado no hace nada en uinput)."""
        return [c for c in self.names("button") if c[1]] + self.names("click")


class FakeDictation:
    def __init__(self):
        self.state, self.message, self.level, self.log = "ready", "", 0.0, []

    def start(self):
        self.log.append("start")
        self.state = "recording"

    def stop(self):
        self.log.append("stop")
        self.state = "ready"


class TestApp(GestureApp):
    name, title, icon = "prueba", "Prueba", "🧪"

    def open(self, ctx):
        super().open(ctx)
        self.frames, self.closed, self.last = 0, False, None

    def update(self, hands, now):
        self.frames += 1
        self.last = hands

    def close(self):
        self.closed = True


class Sim:
    def __init__(self, apps=False, dictation=False, **cfg):
        self.cfg = Config(**cfg)
        self.mouse, self.kbd = Rec(), Rec()
        self.dict = FakeDictation() if dictation else None
        self.ctl = Controller(self.cfg, self.mouse, self.kbd, self.dict)
        self.ctl.app_rect = (0, 0, 1920, 1080)
        self.ctl.set_apps([TestApp] if apps else [])
        self.t = 100.0

    def frames(self, n: int, *hands):
        for _ in range(n):
            self.t += 1 / FPS
            self.ctl.process(result(*hands), ASPECT, self.t)

    def hold(self, seconds: float, *hands):
        self.frames(round(seconds * FPS), *hands)

    def engage(self, x=.5, y=.4, side="right"):
        self.hold(0.6, (side, hand(x, y)))

    def open_menu(self, x=.5, y=.4, side="right"):
        self.hold(0.8, (side, hand(x, y, "fist")))
        assert self.ctl.mode == "launcher", self.ctl.mode
        self.hold(0.4, (side, hand(x, y)))  # abrir la mano: se arma
        assert self.ctl.launcher.armed

    def choose(self, idx: int, x=.5, y=.4, side="right", dist=0.11):
        """Mover la mano en la dirección de la opción `idx` y pellizcar."""
        n = len(self.ctl.launcher.items)
        ang = 2 * math.pi * idx / n
        tx, ty = x + dist * math.sin(ang) / ASPECT, y - dist * math.cos(ang)
        self.hold(0.3, (side, hand(tx, ty)))
        assert self.ctl.launcher.hover == idx, (self.ctl.launcher.hover, idx)
        self.hold(0.2, (side, hand(tx, ty, "index")))
        self.hold(0.3, (side, hand(tx, ty)))


def index_of(sim, kind_or_title):
    items = sim.ctl.launcher.items
    return next(i for i, it in enumerate(items) if kind_or_title in (it.kind, it.title))


# ---------------------------------------------------------------------------

def test_mover_y_clic():
    s = Sim()
    s.engage()
    s.hold(0.3, ("right", hand(.55, .4)))
    assert s.mouse.names("move"), "la mano abierta mueve el cursor"
    s.hold(0.2, ("right", hand(.55, .4, "index")))
    s.hold(0.2, ("right", hand(.55, .4)))
    assert s.mouse.names("button")[:2] == [(272, True), (272, False)], s.mouse.names("button")


def test_puno_abre_menu_y_copia():
    s = Sim()
    s.engage()
    s.open_menu()
    moves = len(s.mouse.names("move"))
    s.choose(index_of(s, "Copiar"))
    assert s.kbd.names("combo") == [("primary+c",)], s.kbd.calls
    assert s.ctl.mode == "mouse"
    assert not s.mouse.presses(), "elegir en el menú no hace clic"
    assert len(s.mouse.names("move")) > moves  # vuelve a mover el cursor


def test_menu_no_se_mueve_el_cursor():
    s = Sim()
    s.engage()
    s.open_menu()
    n = len(s.mouse.names("move"))
    s.hold(0.5, ("right", hand(.6, .3)))
    assert len(s.mouse.names("move")) == n


def test_puno_en_movimiento_no_abre():
    s = Sim()
    s.engage()
    for i in range(30):  # puño desplazándose (agarrando algo) durante 1 s
        s.frames(1, ("right", hand(.3 + i * 0.012, .4, "fist")))
    assert s.ctl.mode == "mouse"


def test_pellizco_al_abrir_puno_no_elige():
    s = Sim()
    s.engage()
    s.hold(0.8, ("right", hand(.5, .4, "fist")))
    assert s.ctl.mode == "launcher"
    # Al abrir el puño sale un pellizco enseguida (sin pasar por mano abierta quieta).
    s.hold(0.1, ("right", hand(.5, .3, "index")))
    s.hold(0.1, ("right", hand(.5, .3)))
    assert s.ctl.mode == "launcher" and not s.kbd.calls


def test_pellizco_al_centro_cierra():
    s = Sim()
    s.engage()
    s.open_menu()
    s.hold(0.2, ("right", hand(.5, .4, "index")))
    assert s.ctl.mode == "mouse" and not s.kbd.calls and not s.mouse.presses()


def test_puno_cierra_menu_y_timeout():
    s = Sim()
    s.engage()
    s.open_menu()
    s.hold(0.8, ("right", hand(.5, .4, "fist")))
    assert s.ctl.mode == "mouse"
    s.hold(0.4, ("right", hand(.5, .4)))
    s.open_menu()
    s.hold(s.cfg.launcher_timeout_s + 0.5, ("right", hand(.5, .4)))
    assert s.ctl.mode == "mouse"


def test_volumen():
    s = Sim()
    s.engage()
    s.open_menu()
    s.choose(index_of(s, "volume"))
    la = s.ctl.launcher
    assert la.page == "volume" and la.armed  # se re-arma donde quedó la mano
    lms = s.ctl.tracks["right"].landmarks
    ox, oy = lms[0].x - BASE[0][0], lms[0].y - BASE[0][1]
    # Mano 0.1 hacia arriba = 3 pasos de 0.03.
    for k in range(10):
        s.frames(1, ("right", hand(ox, oy - 0.01 * (k + 1))))
    s.hold(0.3, ("right", hand(ox, oy - 0.1)))
    ups = s.kbd.names("combo").count(("volumeup",))
    assert ups == 3, s.kbd.calls
    s.hold(0.2, ("right", hand(.5, .3, "index")))
    assert s.ctl.mode == "mouse"


def test_teclado_y_puno_lo_cierra():
    s = Sim()
    s.engage()
    s.open_menu()
    s.choose(index_of(s, "keyboard"))
    assert s.ctl.mode == "keyboard"
    s.hold(0.4, ("right", hand(.5, .4)))
    s.hold(0.8, ("right", hand(.5, .4, "fist")))
    assert s.ctl.mode == "mouse"


def test_apps():
    s = Sim(apps=True)
    s.engage()
    s.open_menu()
    s.choose(index_of(s, "apps"))
    assert s.ctl.launcher.page == "apps"
    s.hold(0.4, ("right", hand(.5, .4)))
    s.choose(index_of(s, "Prueba"))
    app = s.ctl.app
    assert s.ctl.mode == "app" and isinstance(app, TestApp)
    assert app.ctx.rect == (0, 0, 1920, 1080)
    x, y = app.ctx.to_screen(0.5, 0.4)  # centro de la zona activa = centro de la pantalla
    assert abs(x - 960) < 1 and abs(y - 540) < 1, (x, y)
    moves, combos = len(s.mouse.names("move")), len(s.kbd.calls)
    s.hold(0.5, ("right", hand(.4, .4, "index")))  # pellizcos: van a la app
    assert len(s.mouse.names("move")) == moves and len(s.kbd.calls) == combos
    assert app.last and app.last[0].side == "right"
    # Pausa: la app sigue abierta pero recibe "sin manos".
    s.hold(1.1, ("right", hand(.4, .4, "thumbs_up")))
    assert s.ctl.paused
    s.hold(0.3, ("right", hand(.4, .4)))
    assert app.last == []
    s.hold(1.1, ("right", hand(.4, .4, "thumbs_up")))
    assert not s.ctl.paused
    s.hold(0.3, ("right", hand(.4, .4)))
    s.hold(0.8, ("right", hand(.4, .4, "fist")))
    assert app.closed and s.ctl.app is None and s.ctl.mode == "mouse"


def test_app_que_falla_se_cierra():
    class Rota(TestApp):
        def update(self, hands, now):
            raise RuntimeError("bum")

    s = Sim()
    s.ctl.launcher.apps = [Rota]
    s.engage()
    s.ctl.open_app(Rota, s.t)
    s.frames(2, ("right", hand(.5, .4)))
    assert s.ctl.app is None and s.ctl.mode == "mouse" and "falló" in s.ctl.flash_text


def test_sin_apps_no_aparece():
    s = Sim()
    assert all(it.kind != "apps" for it in s.ctl.launcher.visible_items())
    s = Sim(apps=True)
    s.ctl.app_rect = None  # --window
    s.ctl.set_apps([TestApp])
    assert all(it.kind != "apps" for it in s.ctl.launcher.visible_items())


def test_dictado_manos_libres():
    s = Sim(dictation=True)
    s.engage()
    s.open_menu()
    s.choose(index_of(s, "dictate"))
    assert s.dict.state == "recording" and s.ctl.rec_free
    s.hold(1.0)  # sin manos: sigue grabando
    s.engage()
    assert s.dict.state == "recording"
    s.hold(0.2, ("right", hand(.5, .4, "index")))
    s.hold(0.2, ("right", hand(.5, .4)))
    assert s.dict.state == "ready" and s.dict.log == ["start", "stop"]
    assert not s.mouse.presses(), "el pellizco que termina el dictado no hace clic"


def test_swipe_y_descanso():
    s = Sim()
    s.hold(0.6, ("left", hand(.25, .4)), ("right", hand(.7, .4)))
    assert s.ctl.pointer.side == "right"
    # La mano izquierda baja rápido a descansar (sale por abajo): no es un gesto.
    for k in range(6):  # ~2 anchos/s hacia abajo, más que swipe_speed
        s.frames(1, ("left", hand(.25, .4 + 0.12 * k)), ("right", hand(.7, .4)))
    assert s.ctl.swipe_pending, "se detecta como deslizamiento candidato"
    s.hold(1.0, ("right", hand(.7, .4)))
    assert not s.kbd.names("combo"), s.kbd.calls
    # Vuelve, y desliza a la izquierda quedándose en la zona: escritorio anterior.
    s.hold(1.0, ("left", hand(.4, .4)), ("right", hand(.7, .4)))
    for k in range(4):
        s.frames(1, ("left", hand(.4 - 0.08 * k, .4)), ("right", hand(.7, .4)))
    s.hold(0.4, ("left", hand(.15, .4)), ("right", hand(.7, .4)))
    assert s.kbd.names("combo") == [(s.cfg.swipe_left,)], s.kbd.calls


def test_items_personalizados():
    s = Sim(launcher_items=("copiar", "Reabrir=ctrl+shift+t"))
    assert [it.title for it in s.ctl.launcher.visible_items()] == ["Copiar", "Reabrir"]
    try:
        Sim(launcher_items=("nada",))
    except ValueError:
        pass
    else:
        raise AssertionError("opción desconocida debería fallar")


def test_instrumentos_registrados():
    from handctl.apps import load_apps
    names = {cls.name for cls in load_apps()}
    assert {"pizarron", "octapad", "theremin", "sinte", "cuerdas", "estudio"} <= names, names


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as ex:
                failed += 1
                import traceback
                traceback.print_exc()
                print(f"FALLA {name}: {ex!r}")
    sys.exit(1 if failed else 0)

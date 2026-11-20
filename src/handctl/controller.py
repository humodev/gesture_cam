"""Lógica de control: qué mano hace qué y cómo se traducen los gestos.

Solo cuentan las manos "en juego" (palma dentro de la zona activa); una mano
apoyada fuera de la zona queda en reposo y se ignora. Cualquier mano en juego
sola hace todo. Si hay dos, la que ya tenía el cursor lo conserva y la otra
suma clics, scroll, dictado y deslizamientos. En modo teclado cada mano en
juego tiene su propio cursor.

Modos: "mouse", "keyboard", "launcher" (menú radial, ver launcher.py) y "app"
(una GestureApp a pantalla completa recibe las manos; ver apps/base.py).
"""

import math
import threading
import traceback
from collections import deque

from evdev import ecodes as e

from .filters import OneEuro2D
from .gestures import (Debounced, PinchDetector, curled_count, finger_states, hand_pose,
                       is_open_hand, is_v_pose, palm_center, pinch_ratios, to_points)

NO_PINCH = {"index": 9.9, "middle": 9.9, "ring": 9.9, "pinky": 9.9}
from .apps import AppContext
from .keyboard import ROWS, OnScreenKeyboard
from .launcher import Launcher
from .pointer import PrecisionCursor

BACKSPACE = next(k for row in ROWS for k in row if k.code == e.KEY_BACKSPACE)


class HandTrack:
    """Estado de una mano (la izquierda o la derecha del usuario)."""

    def __init__(self, side: str, cfg):
        self.side, self.cfg = side, cfg
        self.filter = OneEuro2D(cfg.filter_min_cutoff, cfg.filter_beta)
        self.pinch = PinchDetector(cfg.pinch_on, cfg.pinch_off, cfg.pinch_confirm_frames)
        self.v_pose = Debounced(3)
        self.history: deque[tuple[float, float, float, float, float]] = deque(maxlen=30)
        self.present = False
        self.last_seen = -math.inf
        self.landmarks = None
        self.ratios: dict[str, float] = {}
        self.states: dict[str, str] = {}
        self.palm = (0.5, 0.5)  # centro de palma, coords de cámara
        self.pos = (0.5, 0.5)   # filtrado y mapeado a la zona activa, [0, 1]
        self.started = self.ended = None
        self.frozen = self.pos
        self.freeze_until = 0.0
        self.pinch_t0 = 0.0
        self.pose: str | None = None   # "fist" / "thumbs_up" confirmada
        self.pose_t0 = 0.0
        self.pose_fired = False
        self._pose_anchor = (0.0, 0.0)
        self._pose_raw: str | None = None
        self._pose_count = 0
        self.repeat_key = None
        self.next_repeat = 0.0
        self.engaged = False
        self.engaged_at = 0.0
        self._zone_switch_t: float | None = None

    def observe(self, landmarks, aspect: float, now: float) -> None:
        cfg = self.cfg
        self.present, self.last_seen, self.landmarks = True, now, landmarks
        pts = to_points(landmarks, aspect)
        self.palm = cx, cy = palm_center(landmarks)
        (rx0, rx1), (ry0, ry1) = cfg.region_x, cfg.region_y
        fx, fy = self.filter((cx - rx0) / (rx1 - rx0) * 1000, (cy - ry0) / (ry1 - ry0) * 1000, now)
        self.pos = (min(max(fx / 1000, 0.0), 1.0), min(max(fy / 1000, 0.0), 1.0))
        self.history.append((now, *self.pos, cx, cy))

        self.ratios, self.states = pinch_ratios(pts), finger_states(pts)
        raw_pose = hand_pose(pts, self.states)
        self._update_pose(raw_pose, now)
        if self.pose and not self.pose_fired:
            # Pose mantenida pero con la mano en movimiento (agarrando algo,
            # yendo a descansar): el conteo vuelve a empezar.
            aspect = cfg.cam_width / cfg.cam_height
            ax, ay = self._pose_anchor
            if math.hypot((cx - ax) * aspect, cy - ay) > cfg.pose_still:
                self.pose_t0, self._pose_anchor = now, (cx, cy)
        # Si los otros tres dedos están cerrados es un puño formándose, no un
        # pellizco: así cerrar la mano no hace clic.
        closest = min(self.ratios, key=self.ratios.get)
        others = {f: s for f, s in self.states.items() if f != closest}
        fist_like = raw_pose is not None or curled_count(others) >= 3
        self.started, self.ended = self.pinch.update(NO_PINCH if fist_like else self.ratios)
        self.v_pose.update(is_v_pose(self.states) and not self.pinch.active)
        if self.started:
            self.frozen = self._lookback(now)
            self.freeze_until = now + cfg.click_freeze_s
            self.pinch_t0 = now
        self._update_engagement(now)

    def _update_pose(self, raw: str | None, now: float) -> None:
        """La pose cambia tras 3 frames seguidos iguales (evita parpadeos)."""
        if raw == self.pose:
            self._pose_count = 0
            return
        self._pose_count = self._pose_count + 1 if raw == self._pose_raw else 1
        self._pose_raw = raw
        if self._pose_count >= 3:
            self.pose, self.pose_t0, self.pose_fired = raw, now, False
            self._pose_anchor = self.palm
            self._pose_count = 0

    def pose_progress(self, now: float) -> float:
        if not self.pose or self.pose_fired:
            return 0.0
        hold = self.cfg.mode_hold_s if self.pose == "fist" else self.cfg.pause_hold_s
        return min((now - self.pose_t0) / hold, 1.0)

    def in_zone(self) -> bool:
        """La palma está AHORA dentro de la zona de juego (sin la demora de `engaged`)."""
        (zx0, zx1), (zy0, zy1) = self.cfg.engage_zone_x, self.cfg.engage_zone_y
        cx, cy = self.palm
        return self.present and zx0 <= cx <= zx1 and zy0 <= cy <= zy1

    def _update_engagement(self, now: float) -> None:
        cfg = self.cfg
        inside = self.in_zone()
        if inside == self.engaged:
            self._zone_switch_t = None
            return
        if self._zone_switch_t is None:
            self._zone_switch_t = now
        if now - self._zone_switch_t >= (cfg.engage_s if inside else cfg.disengage_s):
            self.engaged, self._zone_switch_t = inside, None
            if inside:
                self.engaged_at = now

    def _lookback(self, now: float) -> tuple[float, float]:
        for t, x, y, *_ in reversed(self.history):
            if t <= now - self.cfg.click_lookback_s:
                return x, y
        return self.pos

    def stable_pos(self, now: float) -> tuple[float, float]:
        """Posición a usar para apuntar: congelada justo después de pellizcar."""
        return self.frozen if now < self.freeze_until else self.pos

    def palm_velocity(self, window: float = 0.12) -> tuple[float, float]:
        """Velocidad de la palma en anchos de cámara por segundo."""
        if len(self.history) < 2:
            return 0.0, 0.0
        t1, *_, x1, y1 = self.history[-1]
        for t0, *_, x0, y0 in reversed(self.history):
            if t1 - t0 >= window:
                break
        dt = max(t1 - t0, 1e-3)
        aspect = self.cfg.cam_width / self.cfg.cam_height
        return (x1 - x0) / dt, (y1 - y0) / dt / aspect

    def lose(self) -> None:
        self.present, self.landmarks = False, None
        self.engaged, self._zone_switch_t = False, None
        self.pose, self._pose_raw, self._pose_count = None, None, 0
        self.pinch.reset()
        self.v_pose.reset()
        self.filter.reset()
        self.history.clear()
        self.started = self.ended = None
        self.repeat_key = None


class Controller:
    def __init__(self, cfg, mouse, kbd, dictation=None):
        self.cfg, self.mouse, self.kbd, self.dictation = cfg, mouse, kbd, dictation
        self.lock = threading.Lock()  # el overlay lee el estado desde otro hilo
        self.mode = "mouse"
        self.paused = False
        self.tracks = {"left": HandTrack("left", cfg), "right": HandTrack("right", cfg)}
        self.pointer: HandTrack | None = None   # la mano que mueve el cursor
        self.helpers: list[HandTrack] = []      # otras manos en juego (solo gestos)
        self.cursor = PrecisionCursor(cfg)
        self.screen_freeze_until = 0.0
        self.scroll_hand: HandTrack | None = None
        self.scroll_prev_y = 0.0
        self.scroll_vel = 0.0
        self.inertia = 0.0
        self.swipe_ready_at = 0.0
        self.swipe_pending: tuple[HandTrack, str, float] | None = None  # mano, atajo, cuándo
        self.rec_hand: HandTrack | None = None
        self.rec_free = False   # dictado "manos libres" (desde el lanzador)
        self.osk = OnScreenKeyboard()
        self.kb_rect = (0, 0, 1, 1)
        self.launcher = Launcher(cfg)
        self.launcher.can_dictate = dictation is not None
        self.app = None         # GestureApp abierta
        self.app_error = ""
        # Área para apps en px del overlay; None = no se pueden abrir (modo --window).
        self.app_rect: tuple[int, int, int, int] | None = None
        self.flash_text, self.flash_until = "", 0.0   # aviso breve en pantalla
        self._last_t = None

    def set_apps(self, apps: list) -> None:
        """Apps que ofrece el lanzador (solo si hay dónde dibujarlas)."""
        self.launcher.apps = list(apps) if self.app_rect else []

    # ---------- asignación de manos ----------

    def _assign(self, result, now: float) -> dict[str, object]:
        hands = [(lms, hd[0].category_name.lower(), palm_center(lms))
                 for lms, hd in zip(result.hand_landmarks, result.handedness)]
        if len(hands) >= 2:
            # Imagen espejada: la mano derecha del usuario aparece a la derecha.
            a, b = sorted(hands[:2], key=lambda h: h[2][0])
            return {"left": a[0], "right": b[0]}
        if len(hands) == 1:
            lms, label, (cx, cy) = hands[0]
            recent = [t for t in self.tracks.values() if now - t.last_seen < 0.5]
            if len(recent) == 1 and math.dist((cx, cy), recent[0].palm) < 0.25:
                return {recent[0].side: lms}  # continuidad: evita saltos por lateralidad dudosa
            return {label if label in self.tracks else "right": lms}
        return {}

    def toggle_pause(self) -> None:
        with self.lock:
            self._drop_actions()
            self.paused = not self.paused

    def _release_hand(self, track: HandTrack) -> None:
        """La mano dejó de estar en juego: soltar lo que estuviera haciendo."""
        if track.pinch.active == "index" and self.mode == "mouse":
            self.mouse.release_all()
        if track is self.scroll_hand:
            self.scroll_hand = None
        if track is self.rec_hand and self.dictation:
            self.dictation.stop()
            self.rec_hand = None
        track.pinch.reset()
        track.repeat_key = None
        self.osk.hover.pop(track.side, None)

    def _drop_actions(self) -> None:
        """Suelta todo lo que esté en curso (al cambiar de modo/manos/pausa)."""
        self.mouse.release_all()
        self.scroll_hand, self.inertia = None, 0.0
        self.swipe_pending = None
        if self.dictation and self.dictation.state == "recording":
            self.dictation.stop()
        self.rec_hand, self.rec_free = None, False
        for t in self.tracks.values():
            t.pinch.reset()
            t.repeat_key = None
        self.osk.hover = {}

    def process(self, result, aspect: float, now: float) -> None:
        dt = min(now - self._last_t, 0.1) if self._last_t else 1 / 30
        self._last_t = now

        seen = self._assign(result, now)
        for side, track in self.tracks.items():
            was_engaged = track.engaged
            if side in seen:
                track.observe(seen[side], aspect, now)
            elif track.present and now - track.last_seen > self.cfg.lost_hand_s:
                track.lose()
            else:
                track.started = track.ended = None  # sin dato nuevo en este frame
            if was_engaged and not track.engaged:
                self._release_hand(track)

        engaged = [t for t in self.tracks.values() if t.present and t.engaged]
        if self.pointer not in engaged:
            # El cursor pasa a otra mano en juego (la preferida si llegan juntas).
            preferred = self.tracks.get(self.cfg.dominant_hand)
            pointer = (preferred if preferred in engaged
                       else engaged[0] if engaged else None)
            if pointer is not self.pointer:
                self.cursor.detach()
            self.pointer = pointer
        self.helpers = [t for t in engaged if t is not self.pointer]

        if self.rec_free:  # antes que nada: ese pellizco no debe hacer otra cosa
            self._free_dictation(engaged)
        for hand in engaged:
            self._common_gestures(hand, now)
        if self.paused:
            if self.app:  # pausado: la app ve "sin manos" (suelta notas, etc.)
                self._app_update([], now)
            return
        if self.mode == "app":
            self._app_update(engaged, now)
        elif self.mode == "launcher":
            self._launcher(engaged, now)
        elif self.mode == "mouse":
            self._mouse(engaged, now, dt)
        else:
            self._keyboard(engaged, now)

    # ---------- gestos comunes a ambos modos ----------

    def fist_action(self) -> str:
        """Qué hace ✊ mantenido en el modo actual (también para el overlay)."""
        return {"mouse": "abrir menú", "keyboard": "cerrar teclado",
                "launcher": "cerrar menú", "app": "cerrar app"}[self.mode]

    def _common_gestures(self, hand: HandTrack, now: float) -> None:
        """Poses mantenidas (puño = lanzador/cerrar, pulgar arriba = pausa) y anular (dictado)."""
        if hand.pose and not hand.pose_fired and hand.pose_progress(now) >= 1.0:
            hand.pose_fired = True
            if hand.pose == "thumbs_up":
                self._drop_actions()
                self.launcher.close()
                if self.mode == "launcher":
                    self.mode = "mouse"
                self.paused = not self.paused
            elif not self.paused:
                self._fist(hand, now)
        if self.paused or not self.dictation or self.mode in ("app", "launcher"):
            return
        if hand.started == "ring" and self.rec_hand is None:
            self.dictation.start()
            self.rec_hand = hand
        elif hand.ended == "ring" and hand is self.rec_hand:
            self.dictation.stop()
            self.rec_hand = None

    def _fist(self, hand: HandTrack, now: float) -> None:
        if self.mode == "app":
            self.close_app()
        elif self.mode == "launcher":
            self.launcher.close()
            self._back_to_mouse()
        elif self.mode == "keyboard":
            self._drop_actions()
            self._back_to_mouse()
        else:
            self._drop_actions()
            self.mode = "launcher"
            self.launcher.open(hand, now)

    def _back_to_mouse(self) -> None:
        self.mode = "mouse"
        self.cursor.detach()  # sin salto: el cursor sigue desde donde quedó

    def flash(self, text: str, now: float, seconds: float = 1.2) -> None:
        self.flash_text, self.flash_until = text, now + seconds

    def _free_dictation(self, engaged) -> None:
        """Dictado manos libres: cualquier pellizco lo termina (y no hace clic)."""
        if not (self.dictation and self.dictation.state == "recording"):
            self.rec_free = False
            return
        for hand in engaged:
            if hand.started:
                self.dictation.stop()
                self.rec_free = False
                hand.started = None  # este pellizco solo termina el dictado (sin clic)
                return

    # ---------- lanzador ----------

    def _launcher(self, engaged, now: float) -> None:
        for item in self.launcher.update(engaged, now):
            self._run_item(item, now)
        if not self.launcher.active and self.mode == "launcher":
            self._back_to_mouse()

    def _run_item(self, item, now: float) -> None:
        if item.kind == "combo":
            self.kbd.combo(item.combo)
            if item.title:
                self.flash(f"{item.icon}  {item.title}", now)
        elif item.kind == "keyboard":
            self.mode = "keyboard"
        elif item.kind == "dictate" and self.dictation:
            self.dictation.start()
            self.rec_free = self.dictation.state == "recording"
            if not self.rec_free:
                self.flash(self.dictation.message or "dictado no disponible", now, 2.5)
        elif item.kind == "app":
            self.open_app(item.app, now)

    # ---------- apps ----------

    def open_app(self, cls, now: float) -> None:
        if not self.app_rect:
            return
        self._drop_actions()
        x, y, w, h = self.app_rect
        (rx0, rx1), (ry0, ry1) = self.cfg.region_x, self.cfg.region_y

        def to_screen(xc: float, yc: float) -> tuple[float, float]:
            return (x + (xc - rx0) / (rx1 - rx0) * w, y + (yc - ry0) / (ry1 - ry0) * h)

        try:
            app = cls()
            app.open(AppContext(self.cfg, self.app_rect, to_screen))
        except Exception:
            traceback.print_exc()
            self.flash(f"no se pudo abrir {getattr(cls, 'title', cls.__name__)}", now, 3.0)
            self._back_to_mouse()
            return
        self.app, self.mode, self.app_error = app, "app", ""
        self.launcher.close()

    def close_app(self) -> None:
        app, self.app = self.app, None
        if app is not None:
            try:
                app.close()
            except Exception:
                traceback.print_exc()
        self._drop_actions()
        self._back_to_mouse()

    def _app_update(self, engaged, now: float) -> None:
        try:
            self.app.update(engaged, now)
        except Exception:
            traceback.print_exc()
            self.app_error = f"la app {self.app.title} falló y se cerró"
            self.flash(self.app_error, now, 4.0)
            self.close_app()

    # ---------- modo mouse ----------

    def _mouse(self, engaged, now: float, dt: float) -> None:
        cfg, pointer = self.cfg, self.pointer

        for hand in engaged:
            if hand.started:
                self.inertia = 0.0
        if pointer and pointer.started:
            # Pellizcar con la mano del cursor mueve un poco la palma: volver
            # a donde estaba y congelar un instante.
            self.cursor.rewind(cfg.click_lookback_s, now)
            self.screen_freeze_until = now + cfg.click_freeze_s

        if pointer:
            # Congelado mientras la mano del cursor hace algo que no sea arrastrar.
            hold = (pointer.pinch.active in ("middle", "ring", "pinky")
                    or pointer.v_pose.value or pointer.pose is not None)
            frozen = hold or now < self.screen_freeze_until
            self.mouse.move(*self.cursor.update(pointer.pos, now, frozen=frozen))

        for hand in engaged:
            if hand.started == "index":
                self.mouse.button(e.BTN_LEFT, True)
            elif hand.started == "middle":
                self.mouse.click(e.BTN_RIGHT)
            if hand.ended == "index":
                self.mouse.button(e.BTN_LEFT, False)
            self._scroll(hand, now, dt)
            if hand is not pointer:  # la mano del cursor se mueve rápido a propósito
                self._swipe(hand, now)
        self._confirm_swipe(now)

        if self.inertia and not self.scroll_hand:
            self.mouse.scroll_hires(self.inertia * dt)
            self.inertia *= math.exp(-dt / max(cfg.scroll_inertia_s, 1e-3))
            if abs(self.inertia) < 60:
                self.inertia = 0.0

    def _scroll(self, hand: HandTrack, now: float, dt: float) -> None:
        y = hand.palm[1]
        if hand.v_pose.value and self.scroll_hand in (None, hand):
            if self.scroll_hand is None:
                self.scroll_hand, self.scroll_prev_y, self.scroll_vel = hand, y, 0.0
                self.inertia = 0.0
                return
            amount = (self.scroll_prev_y - y) * self.cfg.scroll_speed
            if self.cfg.scroll_invert:
                amount = -amount
            self.scroll_prev_y = y
            self.mouse.scroll_hires(amount)
            self.scroll_vel = 0.7 * self.scroll_vel + 0.3 * amount / max(dt, 1e-3)
        elif self.scroll_hand is hand:
            self._end_scroll()

    def _end_scroll(self) -> None:
        if self.cfg.scroll_inertia_s > 0 and abs(self.scroll_vel) > 300:
            self.inertia = self.scroll_vel
        self.scroll_hand = None

    def _swipe(self, hand: HandTrack, now: float) -> None:
        cfg = self.cfg
        if (self.swipe_pending or now < self.swipe_ready_at or now - hand.engaged_at < 0.4
                or hand.pinch.active or not is_open_hand(hand.states)):
            return
        vx, vy = hand.palm_velocity()
        combo = None
        if abs(vx) > cfg.swipe_speed and abs(vx) > 2 * abs(vy):
            combo = cfg.swipe_right if vx > 0 else cfg.swipe_left
        elif abs(vy) > cfg.swipe_speed and abs(vy) > 2 * abs(vx):
            combo = cfg.swipe_down if vy > 0 else cfg.swipe_up
        if combo:
            self.swipe_pending = (hand, combo, now)
            self.swipe_ready_at = now + cfg.swipe_cooldown_s

    def _confirm_swipe(self, now: float) -> None:
        """El deslizamiento vale si al rato la mano sigue en la zona: si se fue
        a descansar (abajo) o salió de cuadro, era eso y no un gesto."""
        if not self.swipe_pending:
            return
        hand, combo, t = self.swipe_pending
        if now - t < self.cfg.swipe_confirm_s:
            return
        self.swipe_pending = None
        if hand.in_zone() and now - hand.last_seen < 0.1:
            self.kbd.combo(combo)

    # ---------- modo teclado ----------

    def set_keyboard_rect(self, x: int, y: int, w: int, h: int) -> None:
        self.kb_rect = (x, y, w, h)
        self.osk.layout(x, y, w, h)

    def keyboard_point(self, hand: HandTrack, now: float) -> tuple[int, int]:
        x, y, w, h = self.kb_rect
        px, py = hand.stable_pos(now)
        return int(x + px * (w - 1)), int(y + py * (h - 1))

    def _keyboard(self, engaged, now: float) -> None:
        cfg = self.cfg
        for hand in engaged:
            self.osk.hover[hand.side] = self.osk.key_at(
                *self.keyboard_point(hand, now), self.osk.hover.get(hand.side), cfg.key_stickiness)
            key = (self.osk.hover[hand.side] if hand.started == "index"
                   else BACKSPACE if hand.started == "middle" else None)
            if key is not None:
                if self.osk.press(key, self.kbd, now) == "mode":
                    self._drop_actions()
                    self.mode = "mouse"
                    return
                if key.repeat:
                    hand.repeat_key, hand.next_repeat = key, now + cfg.repeat_delay_s
            if hand.ended:
                hand.repeat_key = None
            if hand.repeat_key and now >= hand.next_repeat:
                self.osk.press(hand.repeat_key, self.kbd, now)
                hand.next_repeat = now + cfg.repeat_interval_s

"""Octapad: 8 pads que se tocan golpeando en el aire, como un tambor.

Un golpe es un movimiento rápido de la mano hacia abajo que frena: suena
el pad donde la mano frena, con volumen según la velocidad del golpe. No
hace falta pellizcar. El kit se cambia dejando la mano quieta sobre su botón.
"""

import threading

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen

from ...audio import SampleVoice, get_engine
from .. import register
from .common import DIM, HAND_COLORS, TEXT, Dwell, InstrumentApp, paint_button, text
from .sounds import KITS, kit_buffers

# Velocidades en alturas de pantalla por segundo.
STRIKE_ON = 0.9       # empieza un golpe
STRIKE_HARD = 3.0     # golpe a volumen máximo
REARM_BELOW = 0.15    # para el próximo golpe la mano tiene que frenar o subir
DECEL = 0.5           # suena cuando la velocidad cae a esta fracción del pico
MAX_STROKE_S = 0.4    # un "golpe" más largo que esto es un movimiento, no un golpe
PAD_MARGIN = 50       # px: un golpe que frena apenas fuera de un pad cuenta
JUMP = 0.3            # desplazamiento en un frame (en alturas de pantalla) que no es un golpe


class Striker:
    """Detecta golpes hacia abajo de una mano a partir de su posición en pantalla."""

    def __init__(self, height: float):
        self.h = height
        self.prev: tuple[float, QPointF] | None = None
        self.v = 0.0
        self.peak = 0.0
        self.t0 = 0.0
        self.in_stroke = False
        self.armed = True

    def update(self, pt: QPointF, now: float) -> float | None:
        """Devuelve la fuerza (0-1) si en este frame terminó un golpe."""
        if self.prev is not None and abs(pt.y() - self.prev[1].y()) + abs(pt.x() - self.prev[1].x()) > JUMP * self.h:
            # Salto imposible para una mano (p.ej. se intercambió con la otra): empezar de cero.
            self.prev, self.v, self.in_stroke, self.armed = None, 0.0, False, True
        if self.prev is not None:
            dt = max(now - self.prev[0], 1e-3)
            raw = (pt.y() - self.prev[1].y()) / dt / self.h
            self.v = 0.5 * self.v + 0.5 * raw
        self.prev = (now, QPointF(pt))
        if self.in_stroke:
            self.peak = max(self.peak, self.v)
            if self.v < self.peak * DECEL:
                self.in_stroke, self.armed = False, False
                return min(max((self.peak - STRIKE_ON) / (STRIKE_HARD - STRIKE_ON), 0.0), 1.0)
            if now - self.t0 > MAX_STROKE_S:
                self.in_stroke = False
            return None
        if not self.armed and self.v < REARM_BELOW:
            self.armed = True
        if self.armed and self.v > STRIKE_ON:
            self.in_stroke, self.peak, self.t0 = True, self.v, now
        return None

    def stroke_progress(self) -> float:
        """Para dibujar: qué tan rápido va bajando la mano (0-1)."""
        return min(max(self.v / STRIKE_ON, 0.0), 1.0)


@register
class Octapad(InstrumentApp):
    name = "octapad"
    title = "Octapad"
    icon = "🥁"
    help = "golpeá hacia abajo sobre un pad (más rápido = más fuerte)  ·  kit: mano quieta sobre su botón"

    def open(self, ctx) -> None:
        super().open(ctx)
        self.engine = get_engine()
        self.kit = "Batería"
        kit_buffers(self.kit)
        # Los demás kits se calculan en segundo plano (Acordes tarda ~0.5 s).
        threading.Thread(target=lambda: [kit_buffers(k) for k in KITS], daemon=True).start()
        self.dwell = Dwell(0.6)
        self.strikers: dict[str, Striker] = {}
        self.hits: dict[int, tuple[float, float]] = {}   # pad → (hasta cuándo brilla, fuerza)
        self.hover: dict[str, int | None] = {}
        a = self.area
        names = list(KITS)
        bw = min(200, (a.width() - 40) / len(names) - 10)
        self.kit_buttons = {k: QRectF(a.left() + 20 + i * (bw + 10), a.top() + 8, bw, 46)
                            for i, k in enumerate(names)}
        pads_area = QRectF(a.left() + 30, a.top() + 80, a.width() - 60, a.height() - 110)
        pw, ph = pads_area.width() / 4, pads_area.height() / 2
        self.pads = [QRectF(pads_area.left() + c * pw, pads_area.top() + r * ph, pw, ph).adjusted(12, 12, -12, -12)
                     for r in range(2) for c in range(4)]

    def close(self) -> None:
        self.engine.clear()

    def _pad_at(self, pt: QPointF) -> int | None:
        best = None
        for i, r in enumerate(self.pads):
            if r.contains(pt):
                return i
            dx = max(r.left() - pt.x(), 0, pt.x() - r.right())
            dy = max(r.top() - pt.y(), 0, pt.y() - r.bottom())
            d = max(dx, dy)
            if d < PAD_MARGIN and (best is None or d < best[0]):
                best = (d, i)
        return best[1] if best else None

    def hit(self, pad: int, force: float, side: str, now: float) -> None:
        buf = kit_buffers(self.kit)[pad]
        amp = 0.3 + 0.7 * force
        pan = -0.5 + (pad % 4) / 3
        self.engine.add(SampleVoice(buf * amp, pan=pan))
        self.hits[pad] = (now + 0.18, force)

    def update(self, hands, now: float) -> None:
        self.hands = list(hands)
        pointers = {h.side: self.palm(h) for h in hands}
        for key in self.dwell.update(pointers, self.kit_buttons, now):
            self.kit = key
        for side in [s for s in self.strikers if s not in pointers]:
            del self.strikers[side]
        self.hover = {}
        for hand in hands:
            pt = pointers[hand.side]
            striker = self.strikers.setdefault(hand.side, Striker(self.ctx.rect[3]))
            force = striker.update(pt, now)
            pad = self._pad_at(pt)
            self.hover[hand.side] = pad
            if force is not None and pad is not None:
                self.hit(pad, force, hand.side, now)

    def paint(self, p: QPainter, now: float) -> None:
        self.paint_frame(p)
        for k, rect in self.kit_buttons.items():
            paint_button(p, rect, k, self.dwell.progress(k, now), active=k == self.kit,
                         hovered=self.dwell.hovered(k))
        labels = [name for name, _ in KITS[self.kit]]
        for i, rect in enumerate(self.pads):
            until, force = self.hits.get(i, (0.0, 0.0))
            lit = max(until - now, 0) / 0.18
            hue = int(i / 8 * 300)
            base = QColor.fromHsv(hue, 120, 70, 235)
            if lit > 0:
                base = QColor.fromHsv(hue, 160, int(120 + 135 * lit * (0.5 + 0.5 * force)), 245)
            hovering = [s for s, pad in self.hover.items() if pad == i]
            p.setPen(QPen(HAND_COLORS[hovering[0]], 4) if hovering else QPen(QColor(255, 255, 255, 50), 1))
            p.setBrush(base)
            grow = 6 * lit
            p.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow), 22, 22)
            text(p, rect, labels[i], 24, TEXT, bold=True)
            text(p, rect.adjusted(0, rect.height() * 0.5, 0, 0), str(i + 1), 12, DIM)
        # Manos: el aro crece con la velocidad hacia abajo (anticipa el golpe).
        for hand in self.hands:
            if not hand.landmarks:
                continue
            pt = self.palm(hand)
            s = self.strikers.get(hand.side)
            c = HAND_COLORS[hand.side]
            if s is not None:
                r = 16 + 22 * s.stroke_progress()
                p.setPen(QPen(c, 3))
                p.setBrush(QColor(0, 0, 0, 0))
                p.drawEllipse(pt, r, r)
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.setBrush(c)
            p.drawEllipse(pt, 12, 12)
            if s is not None and not s.armed:
                p.setPen(QPen(QColor(255, 255, 255, 120), 2))
                p.drawLine(QPointF(pt.x() - 8, pt.y() - 22), QPointF(pt.x() + 8, pt.y() - 22))

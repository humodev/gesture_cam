"""Cuerdas en el aire: creás cuerdas, las estirás o achicás (el largo define
la nota, como una cuerda real: más larga = más grave) y las tocás pasando
la punta de los dedos a través de ellas."""

import math

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen

from ...audio import PENTATONIC, SampleVoice, get_engine, karplus_strong, midi_freq, note_name, snap_to_scale
from .. import register
from .common import DIM, HAND_COLORS, TEXT, InstrumentApp, text

GRAB_RADIUS = 45      # px: distancia para agarrar una punta
MOVE_RADIUS = 30      # px: distancia a la cuerda para agarrarla entera
MIN_LENGTH = 60       # px: más corta que esto al soltar = se borra
PLUCK_COOLDOWN_S = 0.12
PLUCK_TIPS = (8, 12)  # índice y medio
REF_FRACTION = 0.08   # una cuerda de 8% del ancho suena Do6; el doble de largo, una octava abajo


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def _seg_dist(p: QPointF, a: QPointF, b: QPointF) -> tuple[float, float]:
    """Distancia de p al segmento ab y posición (0-1) de la proyección."""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    t = ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / max(dx * dx + dy * dy, 1e-9)
    t = min(max(t, 0.0), 1.0)
    return math.hypot(p.x() - a.x() - t * dx, p.y() - a.y() - t * dy), t


def _crosses(p1: QPointF, p2: QPointF, a: QPointF, b: QPointF) -> bool:
    """¿El movimiento p1→p2 cruzó el segmento ab?"""
    def orient(o, q, r):
        return (q.x() - o.x()) * (r.y() - o.y()) - (q.y() - o.y()) * (r.x() - o.x())
    d1, d2 = orient(a, b, p1), orient(a, b, p2)
    d3, d4 = orient(p1, p2, a), orient(p1, p2, b)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


class AirString:
    _ids = 0

    def __init__(self, p1: QPointF, p2: QPointF, width: float):
        AirString._ids += 1
        self.id = AirString._ids
        self.p1, self.p2, self.width = QPointF(p1), QPointF(p2), width
        self.pluck_t = -10.0
        self.pluck_amp = 0.0
        self.pluck_pos = 0.5

    @property
    def length(self) -> float:
        return _dist(self.p1, self.p2)

    @property
    def midi(self) -> int:
        raw = 84 - 12 * math.log2(max(self.length, 1) / (REF_FRACTION * self.width))
        return snap_to_scale(min(max(raw, 33), 96), PENTATONIC)


@register
class AirStrings(InstrumentApp):
    name = "cuerdas"
    title = "Cuerdas en el aire"
    icon = "≋"
    help = ("pasá el dedo por una cuerda: tocar  ·  pellizco índice en una punta: estirar/achicar "
            "(en el centro: mover)  ·  pellizco medio y arrastrar, o índice con las dos manos: crear")

    def open(self, ctx) -> None:
        super().open(ctx)
        self.engine = get_engine()
        a = self.area
        cx = a.center().x()
        self.strings: list[AirString] = []
        for i, midi in enumerate((60, 64, 67, 72)):  # unas cuerdas para empezar a tocar ya
            length = REF_FRACTION * a.width() * 2 ** ((84 - midi) / 12)
            y = a.top() + a.height() * (0.25 + 0.15 * i)
            self.strings.append(AirString(QPointF(cx - length / 2, y), QPointF(cx + length / 2, y), a.width()))
        self.grabs: dict[str, tuple[AirString, str, QPointF]] = {}  # side → (cuerda, parte, offset)
        self.drawing: dict[str, QPointF] = {}   # side → inicio (crear con una mano)
        self.free_pinch: set[str] = set()       # pellizco índice sin agarrar nada
        self.prev_tips: dict[tuple[str, int], QPointF] = {}
        self.last_pluck: dict[tuple[int, str], float] = {}

    def close(self) -> None:
        self.engine.clear()

    # ---------- interacción ----------

    def _grab_target(self, pt: QPointF):
        best = None
        for s in self.strings:
            for part, end in (("p1", s.p1), ("p2", s.p2)):
                d = _dist(pt, end)
                if d < GRAB_RADIUS and (best is None or d < best[0]):
                    best = (d, s, part)
        if best:
            return best[1], best[2]
        for s in self.strings:
            d, _ = _seg_dist(pt, s.p1, s.p2)
            if d < MOVE_RADIUS:
                return s, "move"
        return None

    def _pluck(self, s: AirString, side: str, speed: float, pos: float, now: float) -> None:
        if now - self.last_pluck.get((s.id, side), -1) < PLUCK_COOLDOWN_S:
            return
        self.last_pluck[(s.id, side)] = now
        amp = min(max(speed / 45, 0.15), 1.0)
        s.pluck_t, s.pluck_amp, s.pluck_pos = now, amp, pos
        a = self.area
        pan = ((s.p1.x() + s.p2.x()) / 2 - a.center().x()) / (a.width() / 2) * 0.7
        samples = karplus_strong(midi_freq(s.midi), amp=0.25 + 0.5 * amp, t60=3.0, brightness=0.35 + 0.5 * amp)
        self.engine.add(SampleVoice(samples, pan=max(min(pan, 1), -1)))

    def update(self, hands, now: float) -> None:
        self.hands = list(hands)
        by_side = {h.side: h for h in hands}
        for side in list(self.grabs):  # mano perdida: soltar
            if side not in by_side:
                self._drop(side)
        for side in list(self.drawing):
            if side not in by_side:
                del self.drawing[side]
        self.free_pinch &= set(by_side)

        for hand in hands:
            side = hand.side
            if hand.started == "index":
                pt = self.pinch_point(hand, "index")
                target = self._grab_target(pt)
                if target:
                    s, part = target
                    ref = s.p1 if part == "p1" else s.p2 if part == "p2" else QPointF(
                        (s.p1.x() + s.p2.x()) / 2, (s.p1.y() + s.p2.y()) / 2)
                    self.grabs[side] = (s, part, QPointF(ref.x() - pt.x(), ref.y() - pt.y()))
                else:
                    self.free_pinch.add(side)
            elif hand.started == "middle":
                self.drawing[side] = self.pinch_point(hand, "middle")

            if hand.ended == "index":
                if side in self.free_pinch and len(self.free_pinch) == 2:
                    self._create_between_hands(by_side)
                self.free_pinch.discard(side)
                self._drop(side)
            if hand.ended == "middle" and side in self.drawing:
                start = self.drawing.pop(side)
                end = self.pinch_point(hand, "middle")
                if _dist(start, end) >= MIN_LENGTH:
                    self.strings.append(AirString(start, end, self.area.width()))

            if side in self.grabs and hand.pinch.active == "index":
                s, part, off = self.grabs[side]
                pt = self.pinch_point(hand, "index")
                target = QPointF(pt.x() + off.x(), pt.y() + off.y())
                if part == "p1":
                    s.p1 = target
                elif part == "p2":
                    s.p2 = target
                else:
                    half = QPointF((s.p2.x() - s.p1.x()) / 2, (s.p2.y() - s.p1.y()) / 2)
                    s.p1, s.p2 = target - half, target + half

            # Tocar: la punta del dedo cruzó una cuerda (solo sin pellizcar).
            for tip in PLUCK_TIPS:
                cur = self.tip(hand, tip)
                prev = self.prev_tips.get((side, tip))
                self.prev_tips[(side, tip)] = cur
                if prev is None or hand.pinch.active:
                    continue
                for s in self.strings:
                    if any(g[0] is s for g in self.grabs.values()):
                        continue
                    if _crosses(prev, cur, s.p1, s.p2):
                        _, pos = _seg_dist(cur, s.p1, s.p2)
                        self._pluck(s, side, _dist(prev, cur), pos, now)
        for key in [k for k in self.prev_tips if k[0] not in by_side]:
            del self.prev_tips[key]

    def _drop(self, side: str) -> None:
        grab = self.grabs.pop(side, None)
        if grab and grab[0].length < MIN_LENGTH and grab[0] in self.strings:
            self.strings.remove(grab[0])  # achicada hasta casi nada: borrar

    def _create_between_hands(self, by_side) -> None:
        a = self.pinch_point(by_side["left"], "index")
        b = self.pinch_point(by_side["right"], "index")
        if _dist(a, b) >= MIN_LENGTH:
            self.strings.append(AirString(a, b, self.area.width()))
        self.free_pinch.clear()

    # ---------- dibujo ----------

    def _string_color(self, s: AirString) -> QColor:
        c = QColor()
        c.setHsv(int((s.midi % 12) / 12 * 330), 170, 255)
        return c

    def paint(self, p: QPainter, now: float) -> None:
        self.paint_frame(p)
        for s in self.strings:
            c = self._string_color(s)
            age = now - s.pluck_t
            amp = s.pluck_amp * 16 * math.exp(-age * 2.5) if age < 3 else 0.0
            dx, dy = s.p2.x() - s.p1.x(), s.p2.y() - s.p1.y()
            length = max(math.hypot(dx, dy), 1)
            nx, ny = -dy / length, dx / length
            path = QPainterPath(s.p1)
            for i in range(1, 33):
                u = i / 32
                off = amp * math.sin(math.pi * u) * math.sin(2 * math.pi * 11 * age)
                path.lineTo(QPointF(s.p1.x() + dx * u + nx * off, s.p1.y() + dy * u + ny * off))
            grabbed = any(g[0] is s for g in self.grabs.values())
            p.setPen(QPen(c, 6 if grabbed else 4))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawPath(path)
            for end in (s.p1, s.p2):
                p.setPen(QPen(QColor(255, 255, 255), 2))
                p.setBrush(c)
                p.drawEllipse(end, 9, 9)
            mid = QPointF(s.p1.x() + dx / 2, s.p1.y() + dy / 2)
            text(p, QRectF(mid.x() - 50, mid.y() - 38, 100, 26), note_name(s.midi), 14, TEXT, bold=True)

        # Cuerdas en construcción.
        by_side = {h.side: h for h in self.hands if h.landmarks}
        p.setPen(QPen(QColor(255, 255, 255, 160), 3))
        for side, start in self.drawing.items():
            if side in by_side:
                p.drawLine(start, self.pinch_point(by_side[side], "middle"))
        if len(self.free_pinch) == 2 and {"left", "right"} <= set(by_side):
            p.drawLine(self.pinch_point(by_side["left"], "index"), self.pinch_point(by_side["right"], "index"))
        if not self.strings:
            text(p, self.area, "Sin cuerdas: pellizcá con el medio y arrastrá para crear una", 18, DIM)
        self.paint_hands(p)
        for hand in self.hands:  # puntas que tocan
            if hand.landmarks and not hand.pinch.active:
                for tip in PLUCK_TIPS:
                    p.setPen(QPen(HAND_COLORS[hand.side], 2))
                    p.setBrush(QColor(255, 255, 255))
                    p.drawEllipse(self.tip(hand, tip), 6, 6)

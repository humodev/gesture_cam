"""Theremín: la posición horizontal de la mano es la nota, la altura el volumen.
Cada mano en juego es una voz (se puede tocar a dúo consigo mismo)."""

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QRadialGradient

from ...audio import MAJOR, ThereminVoice, get_engine, midi_freq, note_name, snap_to_scale
from .. import register
from .common import DIM, HAND_COLORS, TEXT, InstrumentApp, text

LOW, HIGH = 48, 72  # Do3 … Do5
SILENT_ZONE = 0.12  # franja inferior donde la mano no suena


@register
class Theremin(InstrumentApp):
    name = "theremin"
    title = "Theremín"
    icon = "〰"
    help = "← → nota  ·  ↑ ↓ volumen (abajo = silencio)  ·  pellizco índice: afinar a la escala sí/no"

    def open(self, ctx) -> None:
        super().open(ctx)
        self.engine = get_engine()
        self.voices: dict[str, ThereminVoice] = {}
        self.snap = True
        self.state: dict[str, tuple[float, float]] = {}  # side → (midi, volumen)

    def close(self) -> None:
        for v in self.voices.values():
            self.engine.remove(v)
        self.voices.clear()

    def _midi_at(self, x: float) -> float:
        a = self.area
        midi = LOW + (x - a.left()) / a.width() * (HIGH - LOW)
        return min(max(midi, LOW - 1), HIGH + 1)

    def update(self, hands, now: float) -> None:
        for hand in hands:
            if hand.started == "index":
                self.snap = not self.snap
        seen = set()
        for hand in hands:
            seen.add(hand.side)
            voice = self.voices.get(hand.side)
            if voice is None:
                voice = self.voices[hand.side] = self.engine.add(ThereminVoice())
                voice.pan = -0.5 if hand.side == "left" else 0.5
            pt = self.palm(hand)
            midi = self._midi_at(pt.x())
            if self.snap:
                midi = snap_to_scale(midi, MAJOR)
            a = self.area
            height = 1 - (pt.y() - a.top()) / a.height()  # 0 abajo, 1 arriba
            vol = min(max((height - SILENT_ZONE) / (1 - SILENT_ZONE), 0.0), 1.0) ** 1.5
            voice.set(midi_freq(midi), vol * 0.8)
            self.state[hand.side] = (midi, vol)
        for side in list(self.voices):  # mano que salió: silencio
            if side not in seen:
                self.voices[side].set(self.voices[side].target_freq, 0.0)
                self.state.pop(side, None)

    def paint(self, p: QPainter, now: float) -> None:
        self.paint_frame(p)
        a = self.area
        # Líneas de notas: las de la escala más marcadas.
        for m in range(LOW, HIGH + 1):
            x = a.left() + (m - LOW) / (HIGH - LOW) * a.width()
            in_scale = (m % 12) in MAJOR
            alpha = 90 if in_scale else (25 if self.snap else 45)
            p.setPen(QPen(QColor(255, 255, 255, alpha), 2 if m % 12 == 0 else 1))
            p.drawLine(QPointF(x, a.top()), QPointF(x, a.bottom()))
            if in_scale:
                text(p, QRectF(x - 30, a.bottom() - 30, 60, 24), note_name(m), 11, DIM)
        silent_top = a.bottom() - SILENT_ZONE * a.height()
        p.fillRect(QRectF(a.left(), silent_top, a.width(), a.bottom() - silent_top), QColor(0, 0, 0, 90))
        text(p, QRectF(a.left(), silent_top, a.width(), 30), "zona de silencio", 11, DIM)
        text(p, QRectF(a.right() - 260, a.top() + 8, 240, 24),
             "afinado a la escala" if self.snap else "libre (glissando)", 13,
             QColor(120, 230, 140) if self.snap else QColor(255, 200, 80), bold=True)

        for side, (midi, vol) in self.state.items():
            c = HAND_COLORS[side]
            x = a.left() + (midi - LOW) / (HIGH - LOW) * a.width()
            hand_y = a.bottom() - (SILENT_ZONE + vol ** (1 / 1.5) * (1 - SILENT_ZONE)) * a.height()
            r = 18 + 50 * vol
            glow = QRadialGradient(QPointF(x, hand_y), r * 2)
            glow.setColorAt(0, QColor(c.red(), c.green(), c.blue(), int(60 + 150 * vol)))
            glow.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 0))
            p.setPen(QPen(QColor(0, 0, 0, 0)))
            p.setBrush(glow)
            p.drawEllipse(QPointF(x, hand_y), r * 2, r * 2)
            p.setPen(QPen(c, 3))
            p.drawLine(QPointF(x, a.top()), QPointF(x, a.bottom()))
            text(p, QRectF(x - 60, hand_y - r - 40, 120, 30), note_name(midi), 18, TEXT, bold=True)

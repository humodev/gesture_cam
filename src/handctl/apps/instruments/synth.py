"""Sinte XY (estilo Kaoss Pad): mientras pellizcás con el índice suena un
arpegio. Izquierda/derecha elige el acorde, arriba/abajo el brillo del sonido.
Con una segunda mano en juego, su altura controla la velocidad."""

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen

from ...audio import ArpVoice, get_engine
from .. import register
from .common import DIM, HAND_COLORS, TEXT, InstrumentApp, text

# Acordes de Do mayor por grado, como arpegio de 4 notas.
DEGREES = [("Do", 48, (0, 4, 7, 12)), ("Rem", 50, (0, 3, 7, 12)), ("Mim", 52, (0, 3, 7, 12)),
           ("Fa", 53, (0, 4, 7, 12)), ("Sol", 55, (0, 4, 7, 10)), ("Lam", 57, (0, 3, 7, 12)),
           ("Do", 60, (0, 4, 7, 12))]
PATTERN = [0, 1, 2, 3, 2, 1]  # sube y baja


@register
class SynthXY(InstrumentApp):
    name = "sinte"
    title = "Sinte XY"
    icon = "◈"
    help = ("pellizco índice: tocar  ·  ← → acorde  ·  ↑ ↓ brillo  ·  "
            "otra mano: arriba = más rápido  ·  pellizco medio: octava arriba")

    def open(self, ctx) -> None:
        super().open(ctx)
        self.engine = get_engine()
        self.arp = self.engine.add(ArpVoice())
        self.octave = 0
        self.degree = 0
        self.player: str | None = None  # mano que está tocando
        self.cursor: QPointF | None = None
        self.speed_hand_y: float | None = None

    def close(self) -> None:
        self.arp.gate = False
        self.engine.remove(self.arp)

    def update(self, hands, now: float) -> None:
        self.hands = list(hands)
        a = self.area
        player = next((h for h in hands if h.side == self.player), None)
        for hand in hands:
            if hand.started == "middle":
                self.octave = 12 if self.octave == 0 else 0
            if hand.started == "index" and player is None:
                player, self.player = hand, hand.side
        if player and player.pinch.active != "index":
            player, self.player = None, None

        self.arp.gate = player is not None
        self.cursor = None
        if player:
            pt = self.palm(player)
            fx = min(max((pt.x() - a.left()) / a.width(), 0.0), 0.999)
            fy = min(max((pt.y() - a.top()) / a.height(), 0.0), 1.0)
            self.degree = int(fx * len(DEGREES))
            _, root, ivals = DEGREES[self.degree]
            self.arp.notes = [root + self.octave + ivals[i] for i in PATTERN]
            self.arp.brightness = 1 - fy
            self.cursor = pt

        # La altura de otra mano en juego controla la velocidad (2 a 14 notas/s).
        other = next((h for h in hands if h is not player), None)
        if other:
            fy = min(max((self.palm(other).y() - a.top()) / a.height(), 0.0), 1.0)
            self.arp.rate = 2 + (1 - fy) * 12
            self.speed_hand_y = self.palm(other).y()
        else:
            self.speed_hand_y = None

    def paint(self, p: QPainter, now: float) -> None:
        self.paint_frame(p)
        a = self.area
        grad = QLinearGradient(a.topLeft(), a.bottomLeft())
        grad.setColorAt(0, QColor(255, 170, 60, 60))
        grad.setColorAt(1, QColor(40, 60, 160, 60))
        p.fillRect(a, grad)
        col_w = a.width() / len(DEGREES)
        for i, (name, *_) in enumerate(DEGREES):
            x = a.left() + i * col_w
            active = self.arp.gate and i == self.degree
            if active:
                p.fillRect(QRectF(x, a.top(), col_w, a.height()), QColor(255, 255, 255, 40))
            p.setPen(QPen(QColor(255, 255, 255, 50), 1))
            p.drawLine(QPointF(x, a.top()), QPointF(x, a.bottom()))
            text(p, QRectF(x, a.bottom() - 50, col_w, 40), name, 20, TEXT if active else DIM, bold=active)
        text(p, QRectF(a.left() + 10, a.top() + 10, 200, 24), "brillante ↑", 12, DIM)
        text(p, QRectF(a.left() + 10, a.bottom() - 84, 200, 24), "oscuro ↓", 12, DIM)
        info = f"{self.arp.rate:.0f} notas/s" + ("  ·  octava +1" if self.octave else "")
        text(p, QRectF(a.right() - 320, a.top() + 10, 300, 24), info, 14, TEXT, bold=True)
        if self.speed_hand_y is not None:  # barra de velocidad a la derecha
            p.setPen(QPen(QColor(255, 255, 255, 120), 3))
            p.drawLine(QPointF(a.right() - 20, a.top()), QPointF(a.right() - 20, a.bottom()))
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QPointF(a.right() - 20, self.speed_hand_y), 9, 9)
        if self.cursor is not None:
            c = HAND_COLORS[self.player]
            pulse = 26 + 6 * ((now * self.arp.rate) % 1)
            p.setPen(QPen(c, 4))
            p.setBrush(QColor(c.red(), c.green(), c.blue(), 90))
            p.drawEllipse(self.cursor, pulse, pulse)
        self.paint_hands(p)

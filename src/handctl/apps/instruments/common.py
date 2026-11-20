"""Utilidades de dibujo y de manos compartidas por los instrumentos."""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen

from ..base import AppContext, GestureApp

HAND_COLORS = {"left": QColor(255, 90, 200), "right": QColor(80, 220, 255)}
BG = QColor(8, 8, 14, 238)
TEXT = QColor(235, 235, 240)
DIM = QColor(150, 150, 165)


class InstrumentApp(GestureApp):
    """Base de los instrumentos: fondo, título e indicaciones."""

    help = ""

    def open(self, ctx: AppContext) -> None:
        super().open(ctx)
        x, y, w, h = ctx.rect
        self.area = QRectF(x, y + 70, w, h - 70)  # debajo del encabezado
        self.hands: list = []  # manos en juego del último frame (para dibujarlas)

    def screen(self, x: float, y: float) -> QPointF:
        sx, sy = self.ctx.to_screen(x, y)
        return QPointF(sx, sy)

    def palm(self, hand) -> QPointF:
        return self.screen(*hand.palm)

    def tip(self, hand, index: int) -> QPointF:
        lm = hand.landmarks[index]
        return self.screen(lm.x, lm.y)

    def pinch_point(self, hand, finger: str) -> QPointF:
        """Punto medio entre el pulgar y el dedo que pellizca."""
        tip = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}[finger]
        a, b = self.tip(hand, 4), self.tip(hand, tip)
        return QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)

    def paint_frame(self, p: QPainter) -> None:
        x, y, w, h = self.ctx.rect
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BG)
        p.drawRect(QRectF(x, y, w, h))
        p.setPen(TEXT)
        p.setFont(QFont("Sans", 22, QFont.Weight.Bold))
        p.drawText(QRectF(x + 24, y + 10, w - 48, 34), Qt.AlignmentFlag.AlignLeft, f"{self.icon}  {self.title}")
        p.setPen(DIM)
        p.setFont(QFont("Sans", 12))
        p.drawText(QRectF(x + 24, y + 44, w - 48, 22), Qt.AlignmentFlag.AlignLeft,
                   self.help + "   ·   ✊ mantener: salir")

    def paint_hands(self, p: QPainter) -> None:
        for hand in self.hands:
            if hand.landmarks:
                self.paint_hand(p, hand)

    def paint_hand(self, p: QPainter, hand, radius: float = 14, label: str = "") -> None:
        c = HAND_COLORS[hand.side]
        center = self.palm(hand)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(c)
        p.drawEllipse(center, radius, radius)
        for i in (4, 8, 12):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c.red(), c.green(), c.blue(), 160))
            p.drawEllipse(self.tip(hand, i), 5, 5)
        if label:
            p.setPen(TEXT)
            p.setFont(QFont("Sans", 13, QFont.Weight.DemiBold))
            p.drawText(QPointF(center.x() + radius + 6, center.y() - radius), label)


def text(p: QPainter, rect: QRectF, s: str, size: int = 14, color: QColor = TEXT,
         bold: bool = False, align=Qt.AlignmentFlag.AlignCenter) -> None:
    p.setPen(color)
    p.setFont(QFont("Sans", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
    p.drawText(rect, align, s)

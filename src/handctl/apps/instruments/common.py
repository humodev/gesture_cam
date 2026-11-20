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


class Dwell:
    """Botones que se activan dejando la mano quieta encima un instante (sin
    pellizcar). Una vez disparado, hay que salir del botón para repetirlo."""

    def __init__(self, seconds: float = 0.6):
        self.seconds = seconds
        self._target: dict[str, list] = {}  # side → [clave, t0, disparado]

    def update(self, pointers: dict, buttons: dict, now: float) -> list:
        """pointers: side → QPointF; buttons: clave → QRectF. Devuelve claves disparadas."""
        fired = []
        for side, pt in pointers.items():
            key = next((k for k, r in buttons.items() if r.contains(pt)), None)
            cur = self._target.get(side)
            if cur is None or cur[0] != key:
                self._target[side] = [key, now, False]
            elif key is not None and not cur[2] and now - cur[1] >= self.seconds:
                cur[2] = True
                fired.append(key)
        for side in [s for s in self._target if s not in pointers]:
            del self._target[side]
        return fired

    def progress(self, key, now: float) -> float:
        best = 0.0
        for k, t0, done in self._target.values():
            if k == key and not done:
                best = max(best, min((now - t0) / self.seconds, 1.0))
        return best

    def hovered(self, key) -> bool:
        return any(t[0] == key for t in self._target.values())


def paint_button(p: QPainter, rect: QRectF, label: str, progress: float = 0.0,
                 active: bool = False, hovered: bool = False, color: QColor | None = None,
                 size: int = 14) -> None:
    """Botón con relleno de progreso de "mano quieta"."""
    base = color or (QColor(40, 120, 200, 235) if active else QColor(32, 36, 48, 235))
    p.setPen(QPen(QColor(255, 255, 255, 160 if hovered else 40), 2 if hovered else 1))
    p.setBrush(base)
    p.drawRoundedRect(rect, 12, 12)
    if progress > 0:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawRoundedRect(QRectF(rect.x(), rect.y(), rect.width() * progress, rect.height()), 12, 12)
    text(p, rect, label, size, TEXT, bold=active)

"""Pizarrón: escribir y marcar sobre la pantalla (p.ej. al compartir pantalla).

Fondo transparente por defecto: se ve lo que hay debajo. Se dibuja
SEÑALANDO con el índice (los otros dedos cerrados) o pellizcando índice y
pulgar; con la mano abierta el lápiz se levanta. Las herramientas están en
una barra a la derecha y se eligen dejando la punta del índice quieta encima.
Los trazos quedan guardados mientras handctl esté abierto, aunque cierres
la app.
"""

import math
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QStandardPaths, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

from ..filters import OneEuro2D
from . import register
from .base import GestureApp
from .instruments.common import HAND_COLORS, Dwell, paint_button, text

COLORS = [("rojo", QColor(235, 50, 50)), ("amarillo", QColor(255, 210, 40)),
          ("verde", QColor(60, 200, 90)), ("celeste", QColor(60, 180, 255)),
          ("blanco", QColor(250, 250, 250)), ("negro", QColor(20, 20, 20))]
SIZES = [("fino", 3), ("medio", 6), ("grueso", 12)]
TOOLS = [("lápiz", "✎"), ("resaltador", "▬"), ("láser", "◉"), ("borrador", "⌫")]
BACKGROUNDS = ["transparente", "oscuro", "blanco"]
LASER_FADE_S = 1.2
ERASE_RADIUS = 28
PEN_DOWN_FRAMES = 2   # frames seguidos con la pose de dibujar para apoyar el lápiz
PEN_UP_FRAMES = 3     # y sin ella para levantarlo (evita cortes por un frame dudoso)


def is_drawing_pose(hand) -> bool:
    """Señalar con el índice (los otros tres dedos cerrados) o pellizcar índice-pulgar."""
    st = hand.states
    pointing = st.get("index") == "up" and all(st.get(f) in ("down", "mid") for f in ("middle", "ring", "pinky"))
    return pointing or hand.pinch.active == "index"


class Pen:
    """Estado del lápiz de una mano."""

    def __init__(self):
        self.filter = OneEuro2D(1.2, 0.004)  # unidades: px de pantalla
        self.pt: QPointF | None = None
        self.down = False
        self._count = 0
        self.stroke: dict | None = None


@register
class Whiteboard(GestureApp):
    name = "pizarron"
    title = "Pizarrón"
    icon = "✎"

    # Compartido entre aperturas: los dibujos sobreviven a cerrar la app.
    strokes: list[dict] = []
    laser: list[tuple[float, QPointF]] = []

    def open(self, ctx) -> None:
        super().open(ctx)
        self.color = COLORS[0][1]
        self.size = SIZES[1][1]
        self.tool = "lápiz"
        self.background = "transparente"
        self.pens: dict[str, Pen] = {}
        self.dwell = Dwell(0.5)
        self.message, self.message_until = "", 0.0
        self.opened_at = time.monotonic()
        self._layout()

    def _layout(self) -> None:
        x, y, w, h = self.ctx.rect
        bx, bw, bh, gap = x + w - 92, 76, 50, 6
        keys = ([f"color:{n}" for n, _ in COLORS] + [None] + [f"size:{n}" for n, _ in SIZES] + [None]
                + [f"tool:{n}" for n, _ in TOOLS] + [None] + ["undo", "clear", "bg", "save"])
        total = sum(bh + gap if k else 14 for k in keys)
        cy = y + (h - total) / 2
        self.buttons: dict[str, QRectF] = {}
        for k in keys:
            if k is None:
                cy += 14
                continue
            self.buttons[k] = QRectF(bx, cy, bw, bh)
            cy += bh + gap
        self.toolbar = QRectF(bx - 10, self.buttons["color:rojo"].top() - 10, bw + 20, cy - self.buttons["color:rojo"].top() + 4)

    # ---------- acciones ----------

    def _press(self, key: str, now: float) -> None:
        kind, _, val = key.partition(":")
        if kind == "color":
            self.color = dict(COLORS)[val]
            if self.tool in ("borrador", "láser"):
                self.tool = "lápiz"
        elif kind == "size":
            self.size = dict(SIZES)[val]
        elif kind == "tool":
            self.tool = val
        elif key == "undo" and self.strokes:
            self.strokes.pop()
        elif key == "clear":
            self.strokes.clear()
            self._flash("pizarrón borrado", now)
        elif key == "bg":
            self.background = BACKGROUNDS[(BACKGROUNDS.index(self.background) + 1) % len(BACKGROUNDS)]
        elif key == "save":
            self._flash(f"guardado: {self.save()}", now, 4.0)

    def _flash(self, msg: str, now: float, seconds: float = 2.0) -> None:
        self.message, self.message_until = msg, now + seconds

    def save(self) -> str:
        """Guarda los trazos como PNG (fondo transparente salvo que haya fondo)."""
        x, y, w, h = self.ctx.rect
        img = QImage(int(w), int(h), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(-x, -y)
        self._paint_background(p)
        self._paint_strokes(p)
        p.end()
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
                      or Path.home())
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"pizarron-{datetime.now():%Y%m%d-%H%M%S}.png"
        img.save(str(path))
        return path.name

    def _erase_at(self, pt: QPointF) -> None:
        r2 = ERASE_RADIUS ** 2
        self.strokes[:] = [s for s in self.strokes
                           if not any((q.x() - pt.x()) ** 2 + (q.y() - pt.y()) ** 2 < r2 for q in s["points"])]

    # ---------- lógica ----------

    def _tip(self, hand, now: float) -> QPointF:
        lm = hand.landmarks[8]
        sx, sy = self.ctx.to_screen(lm.x, lm.y)
        pen = self.pens[hand.side]
        fx, fy = pen.filter(sx, sy, now)
        return QPointF(fx, fy)

    def update(self, hands, now: float) -> None:
        sides = {h.side for h in hands}
        for side in [s for s in self.pens if s not in sides]:  # mano perdida: levantar lápiz
            del self.pens[side]
        pointers = {}
        for hand in hands:
            pen = self.pens.setdefault(hand.side, Pen())
            pen.pt = self._tip(hand, now)
            if self.toolbar.contains(pen.pt):
                pointers[hand.side] = pen.pt
                pen.down, pen.stroke, pen._count = False, None, 0
                continue
            want = is_drawing_pose(hand)
            if want != pen.down:
                pen._count += 1
                if pen._count >= (PEN_DOWN_FRAMES if want else PEN_UP_FRAMES):
                    pen.down, pen._count = want, 0
                    pen.stroke = None
            else:
                pen._count = 0
            if pen.down:
                self._draw(pen, now)
        for key in self.dwell.update(pointers, self.buttons, now):
            self._press(key, now)
        cutoff = now - LASER_FADE_S
        while self.laser and self.laser[0][0] < cutoff:
            self.laser.pop(0)

    def _draw(self, pen: Pen, now: float) -> None:
        pt = pen.pt
        if self.tool == "láser":
            self.laser.append((now, QPointF(pt)))
            return
        if self.tool == "borrador":
            self._erase_at(pt)
            return
        if pen.stroke is None:
            pen.stroke = {"color": QColor(self.color), "width": self.size,
                          "marker": self.tool == "resaltador", "points": [QPointF(pt)]}
            self.strokes.append(pen.stroke)
            return
        last = pen.stroke["points"][-1]
        if math.hypot(pt.x() - last.x(), pt.y() - last.y()) >= 2:
            pen.stroke["points"].append(QPointF(pt))

    # ---------- dibujo ----------

    def _paint_background(self, p: QPainter) -> None:
        if self.background == "transparente":
            return
        x, y, w, h = self.ctx.rect
        p.fillRect(QRectF(x, y, w, h), QColor(24, 26, 32, 245) if self.background == "oscuro"
                   else QColor(248, 248, 244, 250))

    def _paint_strokes(self, p: QPainter) -> None:
        for s in self.strokes:
            pts = s["points"]
            color = QColor(s["color"])
            width = s["width"] * (3.5 if s["marker"] else 1)
            if s["marker"]:
                color.setAlpha(90)
            pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if len(pts) == 1:
                p.drawPoint(pts[0])
                continue
            path = QPainterPath(pts[0])
            for i in range(1, len(pts) - 1):  # curvas suaves por los puntos medios
                mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2, (pts[i].y() + pts[i + 1].y()) / 2)
                path.quadTo(pts[i], mid)
            path.lineTo(pts[-1])
            p.drawPath(path)

    def _paint_laser(self, p: QPainter, now: float) -> None:
        for i in range(1, len(self.laser)):
            (t0, a), (t1, b) = self.laser[i - 1], self.laser[i]
            if t1 - t0 > 0.15:  # hueco: el láser se "levantó"
                continue
            fade = max(1 - (now - t1) / LASER_FADE_S, 0)
            p.setPen(QPen(QColor(255, 40, 40, int(220 * fade)), 6 * fade + 2, cap=Qt.PenCapStyle.RoundCap))
            p.drawLine(a, b)

    def _button_label(self, key: str) -> tuple[str, bool, QColor | None]:
        kind, _, val = key.partition(":")
        if kind == "color":
            c = dict(COLORS)[val]
            return "", c == self.color and self.tool not in ("borrador", "láser"), c
        if kind == "size":
            return {"fino": "·", "medio": "●", "grueso": "⬤"}[val], self.size == dict(SIZES)[val], None
        if kind == "tool":
            return dict(TOOLS)[val], self.tool == val, None
        return {"undo": "↶", "clear": "✕", "save": "💾",
                "bg": {"transparente": "▢", "oscuro": "■", "blanco": "□"}[self.background]}[key], False, None

    def paint(self, p: QPainter, now: float) -> None:
        self._paint_background(p)
        self._paint_strokes(p)
        self._paint_laser(p, now)
        # Barra de herramientas.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 10, 14, 200))
        p.drawRoundedRect(self.toolbar, 14, 14)
        for key, rect in self.buttons.items():
            label, active, color = self._button_label(key)
            paint_button(p, rect, label, self.dwell.progress(key, now), active=active,
                         hovered=self.dwell.hovered(key), color=color, size=18)
            if active and color is not None:  # color elegido: marco blanco
                p.setPen(QPen(QColor(255, 255, 255), 4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 14, 14)
        # Ayuda los primeros segundos, después solo un recordatorio chico.
        x, y, w, h = self.ctx.rect
        if now - self.opened_at < 6 and not self.strokes:
            box = QRectF(x + w / 2 - 430, y + 30, 860, 64)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(10, 10, 14, 215))
            p.drawRoundedRect(box, 14, 14)
            text(p, box, "☝ señalá con el índice para dibujar  ·  mano abierta: levantar  ·  "
                 "herramientas: índice quieto sobre la barra  ·  ✊ mantener: salir", 13)
        if now < self.message_until:
            box = QRectF(x + w / 2 - 300, y + h - 120, 600, 48)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(10, 10, 14, 215))
            p.drawRoundedRect(box, 12, 12)
            text(p, box, self.message, 14)
        # Punta de cada lápiz.
        for side, pen in self.pens.items():
            if pen.pt is None:
                continue
            if self.tool == "borrador" and not self.toolbar.contains(pen.pt):
                p.setPen(QPen(QColor(255, 255, 255, 200), 2))
                p.setBrush(QColor(255, 255, 255, 40))
                p.drawEllipse(pen.pt, ERASE_RADIUS, ERASE_RADIUS)
            fill = self.color if pen.down and self.tool in ("lápiz", "resaltador") else QColor(0, 0, 0, 0)
            p.setPen(QPen(HAND_COLORS[side], 3))
            p.setBrush(fill)
            p.drawEllipse(pen.pt, 9 if pen.down else 12, 9 if pen.down else 12)

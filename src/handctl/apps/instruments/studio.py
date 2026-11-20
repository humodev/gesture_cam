"""Estudio: looper para armar temas por capas.

Arriba, una barra de botones que se activan con la mano quieta encima
(instrumento, grabar, play/stop, deshacer, borrar, metrónomo, tempo,
compases). Abajo, el instrumento elegido funcionando como siempre. Las manos
que están sobre la barra no tocan el instrumento.

El bucle vive en el motor de audio, así que sigue sonando aunque cambies de
instrumento o cierres el estudio (se detiene con ■ o ✕).
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from ...audio import get_engine
from .. import register
from ..base import AppContext
from .common import BG, DIM, TEXT, Dwell, InstrumentApp, paint_button, text
from .octapad import Octapad
from .strings import AirStrings
from .synth import SynthXY
from .theremin import Theremin

BAR_H = 150
INSTRUMENTS = [Octapad, Theremin, SynthXY, AirStrings]
STATE_COLORS = {"parado": QColor(120, 120, 130), "sonando": QColor(80, 210, 120),
                "cuenta": QColor(255, 200, 60), "armado": QColor(255, 140, 60),
                "grabando": QColor(235, 60, 60)}


@register
class Studio(InstrumentApp):
    name = "estudio"
    title = "Estudio (looper)"
    icon = "🔁"
    help = ("mano quieta sobre un botón: activarlo  ·  ● grabar: cuenta 1 compás y graba una vuelta "
            "(volvé a tocarlo para cancelar)  ·  cada grabación se suma como capa")

    def open(self, ctx) -> None:
        super().open(ctx)
        self.engine = get_engine()
        self.looper = self.engine.looper
        self.dwell = Dwell(0.6)
        self.inst = None
        x, y, w, h = ctx.rect
        self.bar = QRectF(x, y + 70, w, BAR_H - 70)
        self._layout_buttons()
        self._open_instrument(INSTRUMENTS[0])

    def _layout_buttons(self) -> None:
        b = self.bar
        top, hgt = b.top() + 6, 58
        buttons: dict[str, QRectF] = {}
        x = b.left() + 20
        for cls in INSTRUMENTS:
            buttons[f"inst:{cls.name}"] = QRectF(x, top, 150, hgt)
            x += 158
        x += 30
        for key, w in (("rec", 150), ("play", 120), ("undo", 140), ("clear", 120)):
            buttons[key] = QRectF(x, top, w, hgt)
            x += w + 8
        x += 30
        for key, w in (("metro", 160), ("slower", 60), ("faster", 60), ("bars", 130)):
            buttons[key] = QRectF(x, top, w, hgt)
            x += w + 8
        self.buttons = buttons

    def _open_instrument(self, cls) -> None:
        if self.inst is not None:
            self.inst.close()
        x, y, w, h = self.ctx.rect
        self.inst = cls()
        self.inst.open(AppContext(self.ctx.cfg, (x, y + BAR_H, w, h - BAR_H), self.ctx.to_screen))

    def close(self) -> None:
        if self.inst is not None:
            self.inst.close()
            self.inst = None

    def _press(self, key: str) -> None:
        lp = self.looper
        if key.startswith("inst:"):
            cls = next(c for c in INSTRUMENTS if c.name == key[5:])
            if not isinstance(self.inst, cls):
                self._open_instrument(cls)
        elif key == "rec":
            lp.record()
        elif key == "play":
            lp.toggle_play()
        elif key == "undo":
            lp.undo()
        elif key == "clear":
            lp.clear()
        elif key == "metro":
            lp.metronome = not lp.metronome
        elif key == "slower":
            lp.set_tempo(lp.bpm - 5)
        elif key == "faster":
            lp.set_tempo(lp.bpm + 5)
        elif key == "bars" and not lp.layers and lp.recording is None:
            lp.bars = {1: 2, 2: 4, 4: 1}[lp.bars]

    def update(self, hands, now: float) -> None:
        self.hands = list(hands)
        on_bar = [h for h in hands if self.palm(h).y() < self.bar.bottom() + 10]
        for key in self.dwell.update({h.side: self.palm(h) for h in on_bar}, self.buttons, now):
            self._press(key)
        self.inst.update([h for h in hands if h not in on_bar], now)

    # ---------- dibujo ----------

    def _label(self, key: str) -> tuple[str, bool, QColor | None]:
        lp = self.looper
        if key.startswith("inst:"):
            cls = next(c for c in INSTRUMENTS if c.name == key[5:])
            return f"{cls.icon} {cls.title.split()[0]}", isinstance(self.inst, cls), None
        state = lp.state
        if key == "rec":
            busy = state in ("cuenta", "armado", "grabando")
            return ("● cancelar" if busy else "● Grabar"), busy, QColor(170, 30, 30, 235) if busy else None
        if key == "play":
            return ("■ Parar" if lp.playing else "▶ Play"), lp.playing, None
        if key == "undo":
            return "↶ Deshacer", False, None
        if key == "clear":
            return "✕ Borrar", False, None
        if key == "metro":
            return ("♩ Metrónomo" if lp.metronome else "♩ sin metr."), lp.metronome, None
        if key == "slower":
            return "−", False, None
        if key == "faster":
            return "+", False, None
        return f"{lp.bars} compás" if lp.bars == 1 else f"{lp.bars} compases", False, None

    def paint(self, p: QPainter, now: float) -> None:
        self.inst.paint(p, now)  # primero el instrumento (abajo), después la barra encima
        x, y, w, h = self.ctx.rect
        p.setPen(QPen(QColor(0, 0, 0, 0)))
        p.setBrush(BG)
        p.drawRect(QRectF(x, y, w, BAR_H))
        self.paint_frame_header(p)
        for key, rect in self.buttons.items():
            label, active, color = self._label(key)
            paint_button(p, rect, label, self.dwell.progress(key, now), active=active,
                         hovered=self.dwell.hovered(key), color=color, size=13)
        self._paint_timeline(p)
        for hand in self.hands:  # manos sobre la barra
            if hand.landmarks and self.palm(hand).y() < self.bar.bottom() + 10:
                self.paint_hand(p, hand)

    def paint_frame_header(self, p: QPainter) -> None:
        x, y, w, _ = self.ctx.rect
        text(p, QRectF(x + 24, y + 8, 600, 34), f"{self.icon}  {self.title}", 22, TEXT, bold=True,
             align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lp = self.looper
        info = f"{lp.bpm} BPM  ·  {len(lp.layers)} capa{'s' if len(lp.layers) != 1 else ''}  ·  {lp.state}"
        text(p, QRectF(x + w - 620, y + 8, 600, 34), info, 15, STATE_COLORS[lp.state], bold=True,
             align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        text(p, QRectF(x + 24, y + 42, w - 48, 22), self.help + "   ·   ✊ mantener: salir", 11, DIM,
             align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def _paint_timeline(self, p: QPainter) -> None:
        """Barra de posición del bucle con un casillero por pulso."""
        lp = self.looper
        b = self.bar
        top = b.bottom() - 12
        beats = lp.BEATS_PER_BAR * lp.bars
        cw = (b.width() - 40) / beats
        beat = lp.beat_now()
        color = STATE_COLORS[lp.state]
        for i in range(beats):
            r = QRectF(b.left() + 20 + i * cw, top, cw - 3, 8)
            current = lp.playing and beat >= 0 and int(beat) == i
            fill = color if current else QColor(255, 255, 255, 70 if i % lp.BEATS_PER_BAR == 0 else 35)
            p.setPen(QPen(QColor(0, 0, 0, 0)))
            p.setBrush(fill)
            p.drawRect(r)
        if lp.state == "cuenta":
            n = int(-beat) + 1
            text(p, QRectF(b.center().x() - 150, b.bottom() + 40, 300, 120), str(n), 72, color, bold=True)

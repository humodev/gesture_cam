"""Acordes: una grilla de acordes. Con la mano sobre un acorde, pellizco con el
índice = colchón sostenido mientras dure el pellizco; con el medio = rasguido
de cuerdas. Dos manos pueden tocar dos acordes a la vez."""

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QPainter, QPen

from ...audio import PadVoice, SampleVoice, get_engine, karplus_strong, midi_freq
from .. import register
from .common import DIM, HAND_COLORS, TEXT, InstrumentApp, text

# (nombre, fundamental MIDI, intervalos)
MAJ, MIN, DOM7 = (0, 4, 7), (0, 3, 7), (0, 4, 7, 10)
CHORDS = [
    [("Do", 48, MAJ), ("Sol", 43, MAJ), ("Lam", 45, MIN), ("Fa", 41, MAJ)],
    [("Rem", 50, MIN), ("Mim", 52, MIN), ("Mi", 52, MAJ), ("Sol7", 43, DOM7)],
]
STRUM_GAP_S = 0.025


def chord_notes(root: int, intervals) -> list[int]:
    """Voicing abierto: bajo + acorde una octava arriba."""
    return [root] + [root + 12 + i for i in intervals]


@register
class Pads(InstrumentApp):
    name = "acordes"
    title = "Acordes"
    icon = "▦"
    help = "mano sobre un acorde  ·  pellizco índice: sostener  ·  pellizco medio: rasguido"

    def open(self, ctx) -> None:
        super().open(ctx)
        self.engine = get_engine()
        self.held: dict[str, tuple[tuple[int, int], list[PadVoice]]] = {}
        self.hover: dict[str, tuple[int, int] | None] = {}
        self.flash: dict[tuple[int, int], float] = {}
        self.strums: list[tuple[float, SampleVoice]] = []  # rasguidos escalonados

    def close(self) -> None:
        self.engine.clear()
        self.held.clear()

    def _cells(self):
        a = self.area.adjusted(40, 30, -40, -40)
        rows, cols = len(CHORDS), len(CHORDS[0])
        w, h = a.width() / cols, a.height() / rows
        for r in range(rows):
            for c in range(cols):
                yield (r, c), QRectF(a.left() + c * w, a.top() + r * h, w, h).adjusted(10, 10, -10, -10)

    def _cell_at(self, pt) -> tuple[int, int] | None:
        for key, rect in self._cells():
            if rect.adjusted(-10, -10, 10, 10).contains(pt):
                return key
        return None

    def _release(self, side: str) -> None:
        if side in self.held:
            for v in self.held.pop(side)[1]:
                v.stop()

    def update(self, hands, now: float) -> None:
        self.hands = list(hands)
        # Rasguidos pendientes (cada cuerda entra un poco después de la anterior).
        due = [s for s in self.strums if s[0] <= now]
        for item in due:
            self.engine.add(item[1])
        self.strums = [s for s in self.strums if s[0] > now]

        sides = {h.side for h in hands}
        for side in list(self.held):
            if side not in sides:
                self._release(side)
        for hand in hands:
            cell = self._cell_at(self.palm(hand))
            self.hover[hand.side] = cell
            if hand.ended == "index":
                self._release(hand.side)
            if cell is None:
                continue
            name, root, intervals = CHORDS[cell[0]][cell[1]]
            notes = chord_notes(root, intervals)
            pan = -0.4 if hand.side == "left" else 0.4
            if hand.started == "index":
                self._release(hand.side)
                voices = [self.engine.add(PadVoice(midi_freq(m), amp=0.22, pan=pan)) for m in notes]
                self.held[hand.side] = (cell, voices)
                self.flash[cell] = now + 0.2
            elif hand.started == "middle":
                for i, m in enumerate(notes + [notes[-1] + 5]):
                    voice = SampleVoice(karplus_strong(midi_freq(m + 12), amp=0.35, t60=2.0), pan=pan)
                    self.strums.append((now + i * STRUM_GAP_S, voice))
                self.flash[cell] = now + 0.2
        for side in [s for s in self.hover if s not in sides]:
            del self.hover[side]

    def paint(self, p: QPainter, now: float) -> None:
        self.paint_frame(p)
        playing = {cell for cell, _ in self.held.values()}
        for key, rect in self._cells():
            name = CHORDS[key[0]][key[1]][0]
            if key in playing:
                fill = QColor(60, 190, 110, 230)
            elif self.flash.get(key, 0) > now:
                fill = QColor(255, 200, 80, 230)
            else:
                fill = QColor(30, 34, 46, 230)
            hovering = [s for s, c in self.hover.items() if c == key]
            pen = QPen(HAND_COLORS[hovering[0]], 4) if hovering else QPen(QColor(255, 255, 255, 40), 1)
            p.setPen(pen)
            p.setBrush(fill)
            p.drawRoundedRect(rect, 18, 18)
            text(p, rect, name, 34, TEXT, bold=True)
            minor = name.endswith("m")
            text(p, rect.adjusted(0, rect.height() * 0.55, 0, 0),
                 "menor" if minor else ("séptima" if "7" in name else "mayor"), 12, DIM)
        self.paint_hands(p)

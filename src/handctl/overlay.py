"""Overlay de pantalla: una capa transparente, siempre encima, que no recibe
foco ni clics (las teclas y clics siguen yendo a la ventana de abajo).

Corre sobre XWayland como ventana override-redirect, que KWin apila por
encima de las ventanas normales. El tracking corre en otro hilo; acá solo se
dibuja su estado ~30 veces por segundo.
"""

import math
import signal
import threading
import time
import traceback

import numpy as np
from evdev import ecodes as e
from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from .app import help_text, status_text

GLYPHS = {e.KEY_BACKSPACE: "⌫", e.KEY_ENTER: "Enter ⏎", e.KEY_TAB: "Tab ⇥",
          e.KEY_LEFT: "←", e.KEY_RIGHT: "→", e.KEY_UP: "↑", e.KEY_DOWN: "↓",
          e.KEY_SPACE: "espacio"}
KIND_GLYPHS = {"mode": "Mouse", "ctrl": "Ctrl"}
FINGER_COLORS = {"index": QColor(255, 200, 0), "middle": QColor(0, 160, 255),
                 "ring": QColor(255, 70, 70), "pinky": QColor(220, 0, 220)}
HAND_COLORS = {"left": QColor(255, 90, 200), "right": QColor(80, 220, 255)}
LABELS = {"index": "clic", "middle": "clic der.", "ring": "dictado"}
KEY_COLORS = {"normal": QColor(30, 32, 38), "hover": QColor(30, 130, 210),
              "flash": QColor(50, 190, 80), "active": QColor(210, 120, 30)}


class OverlayDisplay:
    """Lo que ve el hilo de control (misma interfaz que WindowDisplay)."""

    full_preview = False

    def __init__(self, cfg, keyboard_rect, size):
        self.cfg = cfg
        self._keyboard_rect = keyboard_rect
        self.size = size  # ancho, alto del overlay: las apps usan todo
        self.ctl = None
        self.image: QImage | None = None
        self.show_preview = cfg.overlay_preview_width > 0
        self.quit = False

    @property
    def preview_width(self) -> int:
        return self.cfg.overlay_preview_width if self.show_preview else 0

    def keyboard_rect(self, _frame_w: int, _frame_h: int):
        return self._keyboard_rect

    def attach(self, ctl) -> None:
        self.ctl = ctl
        ctl.app_rect = (0, 0, *self.size)

    def show(self, img) -> bool:
        if img is None:
            self.image = None
        else:
            img = np.ascontiguousarray(img)
            h, w = img.shape[:2]
            self.image = QImage(img.data, w, h, 3 * w, QImage.Format.Format_BGR888).copy()
        return not self.quit


class Overlay(QWidget):
    def __init__(self, cfg, screen):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.X11BypassWindowManagerHint
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(screen.geometry())
        self.cfg = cfg
        # El mouse virtual es absoluto sobre el escritorio completo (todos los monitores).
        self.desktop = QRect()
        for s in QApplication.screens():
            self.desktop = self.desktop.united(s.geometry())

        W, H = self.width(), self.height()
        kw, kh = int(W * cfg.overlay_keyboard_width), int(H * cfg.overlay_keyboard_height)
        rect = ((W - kw) // 2, H - kh - int(H * 0.07), kw, kh)  # deja libre la barra de tareas
        self.display = OverlayDisplay(cfg, rect, (W, H))
        self._dict_msg, self._dict_msg_t = "", 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(33)
        self._raise_timer = QTimer(self)
        self._raise_timer.timeout.connect(self.raise_)
        self._raise_timer.start(2000)

    # ---------- dibujo ----------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self.display
        if d.ctl is None:  # calibración: la imagen de cámara grande y centrada
            if d.image is not None:
                img = d.image
                p.drawImage(QPointF((self.width() - img.width()) / 2,
                                    (self.height() - img.height()) / 2), img)
            return
        ctl, now = d.ctl, time.monotonic()
        with ctl.lock:
            if ctl.app:
                self._draw_app(p, ctl, now)
            elif ctl.mode == "launcher":
                self._draw_launcher(p, ctl, now)
            elif ctl.mode == "keyboard":
                self._draw_keyboard(p, ctl, now)
            else:
                self._draw_cursor_ring(p, ctl, now)
            self._draw_corner(p, ctl)
            if ctl.paused:
                self._draw_paused(p)
            self._draw_pose_hold(p, ctl, now)
            self._draw_dictation(p, ctl, now)
            if now < ctl.flash_until:
                self._banner(p, ctl.flash_text, self.height() - 200)

    def _draw_app(self, p: QPainter, ctl, now: float) -> None:
        p.save()
        try:
            ctl.app.paint(p, now)
        except Exception:
            traceback.print_exc()
            ctl.app_error = f"la app {ctl.app.title} falló al dibujar y se cerró"
            ctl.flash(ctl.app_error, now, 4.0)
            ctl.close_app()
        finally:
            p.restore()

    def _cursor_point(self, ctl) -> QPointF | None:
        """Posición del cursor del sistema en coordenadas del overlay."""
        if not ctl.cursor.pos:
            return None
        x = self.desktop.x() + ctl.cursor.pos[0] * (self.desktop.width() - 1) - self.x()
        y = self.desktop.y() + ctl.cursor.pos[1] * (self.desktop.height() - 1) - self.y()
        return QPointF(x, y)

    def _banner(self, p: QPainter, text: str, y: float, color=QColor(10, 10, 14, 220)) -> None:
        p.setFont(QFont("Sans", 18, QFont.Weight.DemiBold))
        tw = p.fontMetrics().horizontalAdvance(text) + 48
        box = QRectF(self.width() / 2 - tw / 2, y, tw, 56)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(box, 14, 14)
        p.setPen(QColor(255, 255, 255))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    # ---------- lanzador ----------

    def _draw_launcher(self, p: QPainter, ctl, now: float) -> None:
        la = ctl.launcher
        W, H = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))  # oscurece un poco: "estás en el menú"
        p.drawRect(QRectF(0, 0, W, H))
        if la.page == "help":
            self._draw_help(p, ctl)
            return

        R, bubble, inner = 200.0, 58.0, 74.0
        c = self._cursor_point(ctl) or QPointF(W / 2, H / 2)
        m = R + bubble + 30  # que el menú entre entero en la pantalla
        c = QPointF(min(max(c.x(), m), W - m), min(max(c.y(), m), H - m - 40))

        p.setBrush(QColor(12, 14, 20, 200))
        p.drawEllipse(c, R + bubble + 16, R + bubble + 16)

        if la.page == "volume":
            self._draw_volume(p, la, c, R)
        else:
            n = max(len(la.items), 1)
            if la.hover is not None:  # cuña hacia la opción resaltada
                start = 90 - 360 * la.hover / n - 180 / n
                p.setBrush(QColor(30, 130, 210, 70))
                rr = R + bubble + 16
                p.drawPie(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr),
                          int(start * 16), int(360 / n * 16))
            for i, item in enumerate(la.items):
                ang = 2 * math.pi * i / n
                pos = QPointF(c.x() + R * math.sin(ang), c.y() - R * math.cos(ang))
                hot = i == la.hover
                flash = i == la.chosen and now - la.chosen_at < 0.3
                r = bubble * (1.15 if hot else 1.0)
                fill = (QColor(50, 190, 80) if flash else QColor(30, 130, 210) if hot
                        else QColor(40, 44, 54) if la.armed else QColor(40, 44, 54, 140))
                p.setPen(QPen(QColor(255, 255, 255, 200 if hot else 60), 3 if hot else 1))
                p.setBrush(fill)
                p.drawEllipse(pos, r, r)
                p.setPen(QColor(255, 255, 255, 255 if la.armed else 140))
                p.setFont(QFont("Sans", 24 if hot else 20))
                p.drawText(QRectF(pos.x() - r, pos.y() - r * 0.85, 2 * r, r), Qt.AlignmentFlag.AlignCenter,
                           item.icon)
                p.setFont(QFont("Sans", 11 if hot else 10, QFont.Weight.DemiBold))
                p.drawText(QRectF(pos.x() - r - 10, pos.y() + 2, 2 * r + 20, r * 0.7),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                           | Qt.TextFlag.TextWordWrap, item.title)

        # Centro: zona muerta y un punto que sigue a la mano.
        p.setPen(QPen(QColor(255, 255, 255, 90), 2, Qt.PenStyle.DashLine))
        p.setBrush(QColor(20, 22, 28, 230))
        p.drawEllipse(c, inner, inner)
        dx, dy = la.dot()
        d = math.hypot(dx, dy)
        if d > 0:
            k = min(d * inner, R) / d
            dx, dy = dx * k, dy * k
        hand = la.hand
        color = HAND_COLORS.get(hand.side if hand else "right")
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(color if la.armed else QColor(120, 120, 120))
        p.drawEllipse(QPointF(c.x() + dx, c.y() + dy), 13, 13)
        if hand and hand.ratios and la.armed:
            self._ring(p, QPointF(c.x() + dx, c.y() + dy), hand, 22)

        if not la.armed:
            hint = "abrí la mano"
        elif la.page == "volume":
            hint = "pellizcá: listo"
        elif la.hover is None:
            hint = "mové la mano\nhacia una opción"
        else:
            hint = "pellizcá para\nelegir"
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Sans", 11, QFont.Weight.DemiBold))
        p.drawText(QRectF(c.x() - inner, c.y() - inner, 2 * inner, 2 * inner),
                   Qt.AlignmentFlag.AlignCenter, hint)
        p.setFont(QFont("Sans", 10))
        p.setPen(QColor(220, 220, 220))
        p.drawText(QRectF(c.x() - 250, c.y() + R + bubble + 22, 500, 24), Qt.AlignmentFlag.AlignCenter,
                   "✊ o pellizco al centro: cerrar")

    def _draw_volume(self, p: QPainter, la, c: QPointF, R: float) -> None:
        """Barra vertical: subí o bajá la mano; cada marca es un paso de volumen."""
        bar = QRectF(c.x() + R * 0.55, c.y() - R, 22, 2 * R)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(bar, 11, 11)
        step = R / 8
        for k in range(-8, 9):
            y = c.y() - k * step
            p.setBrush(QColor(80, 220, 255) if (0 < k <= la.volume_level or la.volume_level <= k < 0)
                       else QColor(255, 255, 255, 90))
            p.drawEllipse(QPointF(bar.center().x(), y), 5, 5)
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Sans", 34))
        p.drawText(QRectF(c.x() - R, c.y() - R * 0.95, 2 * R, 60), Qt.AlignmentFlag.AlignCenter, "🔊")
        p.setFont(QFont("Sans", 22, QFont.Weight.Bold))
        p.drawText(QRectF(c.x() - R, c.y() + R * 0.55, 2 * R, 40), Qt.AlignmentFlag.AlignCenter,
                   f"{la.volume_level:+d}")
        p.setFont(QFont("Sans", 11))
        p.drawText(QRectF(c.x() - R, c.y() + R * 0.8, 2 * R, 30), Qt.AlignmentFlag.AlignCenter,
                   "subí o bajá la mano")

    def _draw_help(self, p: QPainter, ctl) -> None:
        """Tarjeta con todos los gestos (opción "Ayuda" del lanzador)."""
        rows = [
            ("🖐  mano abierta, mover", "mover el cursor (lento = preciso)"),
            ("👌  pulgar + índice", "clic · mantener = arrastrar"),
            ("🤏  pulgar + medio", "clic derecho"),
            ("💍  pulgar + anular (mantener)", "dictar mientras lo mantenés"),
            ("✌  mano en V + subir/bajar", "scroll"),
            ("✊  puño quieto ½ s", "este menú · cerrar teclado / app"),
            ("👍  pulgar arriba ~1 s", "pausa / reanudar"),
            ("👋  deslizar la otra mano", "← → escritorios · ↑ vista general · ↓ escritorio"),
            ("😴  bajar una mano", "descansa; la otra sigue sola"),
        ]
        if not ctl.dictation:
            rows = [r for r in rows if "anular" not in r[0]]
        W, H = self.width(), self.height()
        w, lh = 1080, 46
        h = lh * len(rows) + 120
        box = QRectF((W - w) / 2, (H - h) / 2, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 14, 20, 235))
        p.drawRoundedRect(box, 20, 20)
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Sans", 20, QFont.Weight.Bold))
        p.drawText(QRectF(box.x(), box.y() + 16, w, 40), Qt.AlignmentFlag.AlignCenter, "Gestos")
        for i, (gesture, action) in enumerate(rows):
            y = box.y() + 70 + i * lh
            p.setFont(QFont("Sans", 14, QFont.Weight.DemiBold))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(box.x() + 40, y, 440, lh), Qt.AlignmentFlag.AlignVCenter, gesture)
            p.setFont(QFont("Sans", 14))
            p.setPen(QColor(200, 210, 225))
            p.drawText(QRectF(box.x() + 490, y, w - 520, lh), Qt.AlignmentFlag.AlignVCenter, action)
        p.setFont(QFont("Sans", 11))
        p.setPen(QColor(180, 180, 180))
        p.drawText(QRectF(box.x(), box.bottom() - 44, w, 30), Qt.AlignmentFlag.AlignCenter,
                   "pellizcá o hacé puño para cerrar")

    def _draw_dictation(self, p: QPainter, ctl, now: float) -> None:
        """Estado del dictado: grabando (con nivel), transcribiendo o el resultado."""
        d = ctl.dictation
        if not d:
            return
        if d.message != self._dict_msg:
            self._dict_msg, self._dict_msg_t = d.message, now
        if d.state == "recording":
            how = "pellizcá para terminar" if ctl.rec_free else "soltá para terminar"
            self._banner(p, f"🎤  grabando…  ({how})", 170, QColor(150, 20, 20, 220))
            lvl = min(d.level * 25, 1.0)
            bar = QRectF(self.width() / 2 - 150, 238, 300, 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 60))
            p.drawRoundedRect(bar, 4, 4)
            p.setBrush(QColor(255, 90, 90))
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * lvl, 8), 4, 4)
        elif d.state == "transcribing":
            self._banner(p, "✍  transcribiendo…", 170)
        elif d.message and now - self._dict_msg_t < 4.0 and d.state != "loading":
            self._banner(p, d.message[:90], 170)

    def _ring(self, p: QPainter, center: QPointF, track, radius: float) -> None:
        """Aro que se llena al acercar el pulgar a un dedo; lleno = pellizco."""
        if not track.ratios:
            return
        finger, progress = track.pinch.progress(track.ratios)
        color = FINGER_COLORS[finger]
        rect = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
        if track.pinch.active:
            fill = QColor(color)
            fill.setAlpha(150)
            p.setPen(QPen(color, 4))
            p.setBrush(fill)
            p.drawEllipse(rect)
        elif progress > 0.05:
            p.setPen(QPen(color, 5, cap=Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(rect, 90 * 16, int(-360 * 16 * progress))

    def _draw_cursor_ring(self, p: QPainter, ctl, now: float) -> None:
        hands = [h for h in (ctl.pointer, *ctl.helpers) if h and h.ratios]
        center = self._cursor_point(ctl)
        if not (hands and center):
            return
        x, y = center.x(), center.y()
        # El aro muestra la mano que está más cerca de pellizcar.
        actor = max(hands, key=lambda h: (h.pinch.active is not None, h.pinch.progress(h.ratios)[1]))
        posing = max(hands, key=lambda h: h.pose_progress(now))
        if posing.pose_progress(now) > 0:
            # Pose mantenida: aro blanco grande que se completa + qué pose es.
            r = 40
            p.setPen(QPen(QColor(255, 255, 255, 230), 6, cap=Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(x - r, y - r, 2 * r, 2 * r), 90 * 16,
                      int(-360 * 16 * posing.pose_progress(now)))
            p.setFont(QFont("Sans", 20))
            p.drawText(QPointF(x + 44, y + 10), "✊" if posing.pose == "fist" else "👍")
            return
        self._ring(p, center, actor, 26)
        if ctl.scroll_hand or actor.v_pose.value:
            p.setPen(QColor(255, 255, 0))
            p.setFont(QFont("Sans", 18, QFont.Weight.Bold))
            p.drawText(QPointF(x + 30, y + 8), "↕")
        elif actor.pinch.active in LABELS:
            p.setPen(FINGER_COLORS[actor.pinch.active])
            p.setFont(QFont("Sans", 12, QFont.Weight.Bold))
            p.drawText(QPointF(x + 32, y + 6), LABELS[actor.pinch.active])

    def _draw_keyboard(self, p: QPainter, ctl, now: float) -> None:
        cfg, osk = self.cfg, ctl.osk
        x, y, w, h = self.display.keyboard_rect(0, 0)
        alpha = int(255 * cfg.overlay_opacity)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 10, 14, int(alpha * 0.6)))
        p.drawRoundedRect(QRectF(x - 14, y - 44, w + 28, h + 58), 18, 18)

        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Sans", 11))
        p.drawText(QRectF(x, y - 38, w, 30), Qt.AlignmentFlag.AlignVCenter, help_text(ctl))

        key_h = h / 5
        for k, (x1, y1, x2, y2) in osk.keys():
            color = QColor(KEY_COLORS[osk.style(k, now)])
            color.setAlpha(alpha)
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(color)
            r = QRectF(x1 + 3, y1 + 3, x2 - x1 - 6, y2 - y1 - 6)
            p.drawRoundedRect(r, 10, 10)
            label = GLYPHS.get(k.code) or KIND_GLYPHS.get(k.kind) or osk.label(k)
            if k.kind == "shift":
                label = "⇧ " + osk.label(k)
            size = key_h * (0.36 if len(label) == 1 else 0.22)
            p.setFont(QFont("Sans", max(int(size * 0.75), 8), QFont.Weight.DemiBold))
            p.setPen(QColor(255, 255, 255))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)

        for track in ctl.tracks.values():
            if not track.engaged:
                continue
            px, py = ctl.keyboard_point(track, now)
            center = QPointF(px, py)
            self._ring(p, center, track, 24)
            p.setPen(QPen(QColor(255, 255, 255), 3))
            p.setBrush(HAND_COLORS[track.side])
            p.drawEllipse(center, 11, 11)

    def _draw_corner(self, p: QPainter, ctl) -> None:
        """Cámara en miniatura + estado, arriba a la derecha."""
        x = self.width() - 16
        y = 16
        img = self.display.image
        if img is not None:
            x = self.width() - img.width() - 16
            p.setOpacity(0.9)
            p.drawImage(QPointF(x, y), img)
            p.setOpacity(1.0)
            y += img.height() + 6
        text = status_text(ctl, getattr(ctl, "fps", 0.0))
        p.setFont(QFont("Sans", 11, QFont.Weight.DemiBold))
        tw = p.fontMetrics().horizontalAdvance(text) + 20
        box = QRectF(self.width() - tw - 16, y, tw, 28)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 10, 14, 190))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor(255, 255, 255))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_pose_hold(self, p: QPainter, ctl, now: float) -> None:
        """Barra de progreso mientras se mantiene puño o pulgar arriba."""
        hands = [t for t in ctl.tracks.values() if t.engaged and t.pose_progress(now) > 0]
        if not hands:
            return
        hand = max(hands, key=lambda t: t.pose_progress(now))
        if hand.pose == "thumbs_up":
            text = "👍  " + ("reanudar" if ctl.paused else "pausar")
        elif ctl.paused:
            return
        else:
            text = "✊  " + ctl.fist_action()
        box = QRectF(self.width() / 2 - 180, 90, 360, 64)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 10, 14, 210))
        p.drawRoundedRect(box, 14, 14)
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Sans", 16, QFont.Weight.DemiBold))
        p.drawText(QRectF(box.x(), box.y() + 6, box.width(), 32), Qt.AlignmentFlag.AlignCenter, text)
        bar = QRectF(box.x() + 20, box.y() + 44, box.width() - 40, 8)
        p.setBrush(QColor(255, 255, 255, 50))
        p.drawRoundedRect(bar, 4, 4)
        p.setBrush(QColor(80, 220, 255))
        p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * hand.pose_progress(now), 8), 4, 4)

    def _draw_paused(self, p: QPainter) -> None:
        p.setPen(QPen(QColor(230, 40, 40, 200), 8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(4, 4, self.width() - 8, self.height() - 8))
        p.setFont(QFont("Sans", 20, QFont.Weight.Bold))
        box = QRectF(self.width() / 2 - 260, 24, 520, 48)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(150, 20, 20, 210))
        p.drawRoundedRect(box, 12, 12)
        p.setPen(QColor(255, 255, 255))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, "PAUSA · pulgar arriba para seguir")


def _pick_screen(name: str):
    for s in QApplication.screens():
        if name and s.name() == name:
            return s
    if name:
        names = ", ".join(s.name() for s in QApplication.screens())
        print(f"overlay_screen '{name}' no existe (hay: {names}); uso el principal.")
    return QApplication.primaryScreen()


def _tray_icon() -> QIcon:
    icon = QIcon.fromTheme("input-mouse")
    if icon.isNull():
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(80, 220, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(8, 8, 48, 48)
        p.end()
        icon = QIcon(pix)
    return icon


def run_overlay(cfg, worker) -> None:
    """Muestra el overlay y corre `worker(display)` (el bucle de control) en otro hilo."""
    app = QApplication.instance() or QApplication(["handctl"])
    app.setQuitOnLastWindowClosed(False)
    overlay = Overlay(cfg, _pick_screen(cfg.overlay_screen))
    overlay.show()
    display = overlay.display

    def target():
        try:
            worker(display)
        except Exception:
            traceback.print_exc()

    thread = threading.Thread(target=target, name="handctl-control", daemon=True)
    thread.start()
    watchdog = QTimer()
    watchdog.timeout.connect(lambda: None if thread.is_alive() else app.quit())
    watchdog.start(200)
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    menu = QMenu()
    pause = QAction("Pausar / reanudar", menu)
    pause.triggered.connect(lambda: display.ctl and display.ctl.toggle_pause())
    camera = QAction("Mostrar / ocultar cámara", menu)
    camera.triggered.connect(lambda: setattr(display, "show_preview", not display.show_preview))
    quit_ = QAction("Salir", menu)
    quit_.triggered.connect(app.quit)
    for action in (pause, camera, quit_):
        menu.addAction(action)
    tray = QSystemTrayIcon(_tray_icon())
    tray.setToolTip("handctl")
    tray.setContextMenu(menu)
    tray.show()

    app.exec()
    display.quit = True
    thread.join(timeout=3)
    tray.hide()

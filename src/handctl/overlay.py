"""Overlay de pantalla: una capa transparente, siempre encima, que no recibe
foco ni clics (las teclas y clics siguen yendo a la ventana de abajo).

Corre sobre XWayland como ventana override-redirect, que KWin apila por
encima de las ventanas normales. El tracking corre en otro hilo; acá solo se
dibuja su estado ~30 veces por segundo.
"""

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
KEY_COLORS = {"normal": QColor(30, 32, 38), "hover": QColor(30, 130, 210),
              "flash": QColor(50, 190, 80), "active": QColor(210, 120, 30)}


class OverlayDisplay:
    """Lo que ve el hilo de control (misma interfaz que WindowDisplay)."""

    full_preview = False

    def __init__(self, cfg, keyboard_rect):
        self.cfg = cfg
        self._keyboard_rect = keyboard_rect
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
        self.display = OverlayDisplay(cfg, rect)

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
            if ctl.mode == "keyboard":
                self._draw_keyboard(p, ctl, now)
            else:
                self._draw_cursor_ring(p, ctl)
            self._draw_corner(p, ctl)
            if ctl.paused:
                self._draw_paused(p)
            self._draw_pose_hold(p, ctl, now)

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

    def _draw_cursor_ring(self, p: QPainter, ctl) -> None:
        hands = [h for h in (ctl.pointer, *ctl.helpers) if h and h.ratios]
        if not (hands and ctl.cursor.pos):
            return
        # El aro muestra la mano que está más cerca de pellizcar.
        actor = max(hands, key=lambda h: (h.pinch.active is not None, h.pinch.progress(h.ratios)[1]))
        x = self.desktop.x() + ctl.cursor.pos[0] * (self.desktop.width() - 1) - self.x()
        y = self.desktop.y() + ctl.cursor.pos[1] * (self.desktop.height() - 1) - self.y()
        center = QPointF(x, y)
        self._ring(p, center, actor, 26)
        if ctl.scroll_hand or actor.v_pose.value:
            p.setPen(QColor(255, 255, 0))
            p.setFont(QFont("Sans", 18, QFont.Weight.Bold))
            p.drawText(QPointF(x + 30, y + 8), "↕")

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
            text = "✊  " + ("abrir teclado" if ctl.mode == "mouse" else "cerrar teclado")
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

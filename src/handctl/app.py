"""Bucle principal: cámara → MediaPipe → gestos → mouse/teclado virtual."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")  # la ventana de OpenCV corre sobre XWayland

import argparse
import unicodedata
from pathlib import Path

import cv2

from .calibrate import run_calibration
from .capture import MODEL_PATH, HandCapture
from .config import CONFIG_PATH, load_config
from .controller import Controller
from .keyboard import KEYCODES
from .vinput import VirtualKeyboard, VirtualMouse, parse_combo, settle

WINDOW = "handctl"
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
]
FINGER_COLORS = {"index": (0, 200, 255), "middle": (255, 160, 0),
                 "ring": (60, 60, 255), "pinky": (220, 0, 220)}
HELP = {
    "mouse": "índice: clic/arrastrar · medio: clic der. · anular: dictar · V: scroll · puño: teclado · pulgar arriba: pausa",
    "keyboard": "índice: tecla · medio: borrar · anular: dictar · puño: cerrar teclado · pulgar arriba: pausa",
}


class _NullDevice:
    """Sustituto de los dispositivos virtuales para --dry-run."""
    pressed: set = set()

    def __getattr__(self, _name):
        return lambda *a, **k: None


def ascii_text(s: str) -> str:
    """putText de OpenCV no dibuja tildes."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def put(img, text: str, org, scale: float = 0.7, color=(255, 255, 255)) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2 if scale >= 0.6 else 1,
                cv2.LINE_AA)


def draw_hand(img, track, color, label: str) -> None:
    h, w = img.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in track.landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(img, pts[a], pts[b], color, 2, cv2.LINE_AA)
    for p in pts:
        cv2.circle(img, p, 3, (255, 255, 255), -1, cv2.LINE_AA)
    if label:
        put(img, label, (pts[0][0] - 30, pts[0][1] + 25), 0.5, color)
    # Aro de pellizco: se llena a medida que el pulgar se acerca a un dedo.
    if track.ratios:
        finger, progress = track.pinch.progress(track.ratios)
        center = (int(track.palm[0] * w), int(track.palm[1] * h))
        fcolor = FINGER_COLORS[finger]
        if track.pinch.active:
            cv2.circle(img, center, 18, fcolor, -1, cv2.LINE_AA)
        elif progress > 0:
            cv2.ellipse(img, center, (18, 18), -90, 0, 360 * progress, fcolor, 4, cv2.LINE_AA)
        if track.v_pose.value:
            put(img, "V", (center[0] - 8, center[1] + 8), 0.7, (0, 255, 255))
        if track.pose:
            name = "PUNO" if track.pose == "fist" else "PULGAR ARRIBA"
            put(img, name, (center[0] - 40, center[1] - 28), 0.6, (0, 255, 255))


def render(frame, ctl: Controller, cfg, fps: float, now: float, width: int, full: bool = True):
    """Vista de cámara anotada. `full=False`: solo manos y zona (miniatura del
    overlay, que dibuja el teclado y el estado por su cuenta)."""
    h, w = frame.shape[:2]
    dw = width
    dh = round(h * dw / w)
    img = cv2.resize(frame, (dw, dh))
    for track in ctl.tracks.values():
        if not track.landmarks:
            continue
        if not track.engaged:
            color, label = (140, 140, 140), "reposo"
        elif ctl.mode == "keyboard":
            color, label = (0, 255, 0), ""
        elif track is ctl.pointer:
            color, label = (0, 255, 0), "cursor"
        else:
            color, label = (255, 0, 255), "gestos"
        draw_hand(img, track, color, label)

    if ctl.mode == "mouse":
        (rx0, rx1), (ry0, ry1) = cfg.region_x, cfg.region_y
        cv2.rectangle(img, (int(rx0 * dw), int(ry0 * dh)), (int(rx1 * dw), int(ry1 * dh)),
                      (255, 200, 0), 1)
        if ctl.cursor.pos and ctl.pointer:
            cx = int((rx0 + ctl.cursor.pos[0] * (rx1 - rx0)) * dw)
            cy = int((ry0 + ctl.cursor.pos[1] * (ry1 - ry0)) * dh)
            cv2.circle(img, (cx, cy), 7, (0, 0, 255), -1, cv2.LINE_AA)
    elif full:
        ctl.osk.draw(img, now)
        for track in ctl.tracks.values():
            if track.engaged:
                p = ctl.keyboard_point(track, now)
                cv2.circle(img, p, 9, (0, 0, 255), -1, cv2.LINE_AA)
                cv2.circle(img, p, 10, (255, 255, 255), 2, cv2.LINE_AA)

    if ctl.paused:
        cv2.rectangle(img, (0, 0), (dw - 1, dh - 1), (0, 0, 255), 6)
    if not full:
        return img
    put(img, status_text(ctl, fps), (12, 30), 0.75)

    d = ctl.dictation
    if d:
        if d.state == "recording":
            cv2.circle(img, (22, 58), 8, (0, 0, 255), -1, cv2.LINE_AA)
            bar = int(min(d.level * 2000, 200))
            cv2.rectangle(img, (40, 50), (40 + bar, 66), (0, 0, 255), -1)
            put(img, "grabando", (250, 64), 0.6)
        elif d.state in ("loading", "transcribing"):
            put(img, "transcribiendo..." if d.state == "transcribing" else d.message, (12, 64), 0.6,
                (0, 255, 255))
        elif d.message:
            put(img, ascii_text(d.message)[:80], (12, 64), 0.6, (200, 255, 200))

    put(img, ascii_text(help_text(ctl)).replace("·", "|"), (10, dh - 10), 0.45, (220, 220, 220))
    return img


SIDE_NAMES = {"left": "izq", "right": "der"}


def status_text(ctl: Controller, fps: float) -> str:
    status = f"{'MOUSE' if ctl.mode == 'mouse' else 'TECLADO'}  {fps:4.1f} fps  "
    if ctl.pointer:
        status += f"cursor: {SIDE_NAMES[ctl.pointer.side]}"
        if ctl.helpers:
            status += f" + gestos: {SIDE_NAMES[ctl.helpers[0].side]}"
    else:
        status += "sin mano en la zona"
    resting = [SIDE_NAMES[t.side] for t in ctl.tracks.values() if t.present and not t.engaged]
    if resting:
        status += f"  ({', '.join(resting)} en reposo)"
    if ctl.paused:
        status += "  PAUSA (pulgar arriba para seguir)"
    elif ctl.scroll_hand:
        status += "  [scroll]"
    return status


def help_text(ctl: Controller) -> str:
    line = HELP[ctl.mode]
    return line if ctl.dictation else line.replace("anular: dictar · ", "")


class WindowDisplay:
    """Vista previa en una ventana de OpenCV (modo --window)."""

    full_preview = True

    def __init__(self, cfg):
        self.cfg = cfg
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        try:
            cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
        except cv2.error:
            pass

    @property
    def preview_width(self) -> int:
        return self.cfg.preview_width

    def keyboard_rect(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        dw = self.cfg.preview_width
        dh = round(frame_h * dw / frame_w)
        return 10, int(dh * 0.36), dw - 20, int(dh * 0.56)

    def attach(self, ctl) -> None:
        pass

    def show(self, img) -> bool:
        cv2.imshow(WINDOW, img)
        if cv2.waitKey(1) & 0xFF == 27:
            return False
        return cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) >= 1

    def close(self) -> None:
        cv2.destroyAllWindows()


def run(cfg, args, capture, display) -> None:
    """Calibración (opcional) + bucle de control. Corre en el hilo que sea."""
    if args.calibrate and not run_calibration(cfg, capture, display.show):
        return
    combos = [cfg.swipe_left, cfg.swipe_right, cfg.swipe_up, cfg.swipe_down, cfg.voice_paste]
    combo_codes = {parse_combo(c)[1] for c in combos}
    if args.dry_run:
        mouse, kbd = _NullDevice(), _NullDevice()
    else:
        mouse, kbd = VirtualMouse(), VirtualKeyboard(KEYCODES | combo_codes)
        settle()
    dictation = None
    if cfg.voice_enabled:
        from .voice import Dictation
        dictation = Dictation(cfg, kbd, paste=not args.dry_run)

    ctl = Controller(cfg, mouse, kbd, dictation)
    display.attach(ctl)
    fps, last = 0.0, None
    try:
        for now, frame, result in capture:
            if last is not None:
                fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
            last = now
            h, w = frame.shape[:2]
            if ctl.kb_rect == (0, 0, 1, 1):
                ctl.set_keyboard_rect(*display.keyboard_rect(w, h))
            with ctl.lock:
                ctl.process(result, w / h, now)
                ctl.fps = fps
                img = render(frame, ctl, cfg, fps, now, display.preview_width,
                             full=display.full_preview) if display.preview_width else None
            if not display.show(img):
                break
    finally:
        if dictation and dictation.state == "recording":
            dictation.stop()
        mouse.close()
        kbd.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Controlá el mouse y el teclado con las manos.")
    ap.add_argument("--camera", type=int, help="índice de la cámara (default 0)")
    ap.add_argument("--model", type=Path, default=MODEL_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="no mueve el mouse ni escribe: solo vista previa (para practicar)")
    ap.add_argument("--calibrate", action="store_true",
                    help=f"calibrar zona y pellizco (se guarda en {CONFIG_PATH})")
    ap.add_argument("--voice", action=argparse.BooleanOptionalAction, default=None,
                    help="dictado con Whisper en la GPU (activado por defecto)")
    ap.add_argument("--window", action="store_true",
                    help="usar la ventana de vista previa en lugar del overlay de pantalla")
    args = ap.parse_args()

    cfg = load_config()
    if args.camera is not None:
        cfg.camera = args.camera
    if args.voice is not None:
        cfg.voice_enabled = args.voice
    for combo in (cfg.swipe_left, cfg.swipe_right, cfg.swipe_up, cfg.swipe_down, cfg.voice_paste):
        parse_combo(combo)  # valida los atajos antes de abrir la cámara

    capture = HandCapture(cfg, args.model)
    try:
        if args.window:
            display = WindowDisplay(cfg)
            print("Esc en la ventana de vista previa (o Ctrl+C acá) para salir.")
            try:
                run(cfg, args, capture, display)
            except KeyboardInterrupt:
                pass
            finally:
                display.close()
        else:
            from .overlay import run_overlay
            print("Salir: ícono de handctl en la bandeja o Ctrl+C acá.")
            run_overlay(cfg, lambda display: run(cfg, args, capture, display))
    finally:
        capture.close()

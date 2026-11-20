"""Calibración guiada: zona cómoda de la mano y umbrales de pellizco."""

import cv2
import numpy as np

from .config import CONFIG_PATH, save_overrides
from .gestures import palm_center, pinch_ratios, to_points

STEPS = [
    ("zona", "Move la mano con la que apuntas por toda la zona COMODA",
     "(arriba, abajo, a los costados, sin estirar el brazo)", 7.0),
    ("pinch", "Pellizca indice y pulgar varias veces",
     "(juntar y separar bien, a ritmo normal)", 6.0),
]
COUNTDOWN_S = 3.0
MIN_REGION = (0.45, 0.40)  # ancho y alto mínimos de la zona (fracción de imagen)


def _text(img, line: str, y: int, scale: float = 0.8) -> None:
    cv2.putText(img, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(img, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)


def _widen(a: float, b: float, minimum: float) -> tuple[float, float]:
    """Agranda [a, b] alrededor de su centro hasta `minimum`, sin salir de [0, 1]."""
    if b - a >= minimum:
        return a, b
    c = min(max((a + b) / 2, minimum / 2), 1 - minimum / 2)
    return c - minimum / 2, c + minimum / 2


def run_calibration(cfg, capture, show) -> bool:
    """`show(img) -> bool` muestra cada frame; False = cancelar."""
    samples: dict[str, list] = {name: [] for name, *_ in STEPS}
    frames = iter(capture)
    for name, title, hint, duration in STEPS:
        start = None
        for now, frame, result in frames:
            start = start or now
            elapsed = now - start
            h, w = frame.shape[:2]
            img = cv2.resize(frame, (cfg.preview_width, round(h * cfg.preview_width / w)))
            _text(img, title, 40)
            _text(img, hint, 75, 0.6)
            if elapsed < COUNTDOWN_S:
                _text(img, f"Empieza en {COUNTDOWN_S - elapsed:.0f}...", 130, 1.2)
            elif result.hand_landmarks:
                lms = result.hand_landmarks[0]
                if name == "zona":
                    samples[name].append(palm_center(lms))
                    if len(samples[name]) > 5:  # recuadro recorrido hasta ahora
                        pts = np.array(samples[name])
                        (x0, y0), (x1, y1) = pts.min(axis=0), pts.max(axis=0)
                        iw, ih = img.shape[1], img.shape[0]
                        cv2.rectangle(img, (int(x0 * iw), int(y0 * ih)), (int(x1 * iw), int(y1 * ih)),
                                      (255, 200, 0), 2)
                else:
                    samples[name].append(pinch_ratios(to_points(lms, w / h))["index"])
                left = COUNTDOWN_S + duration - elapsed
                _text(img, f"Grabando... {left:.0f}s", 130, 1.2)
                for lm in lms:
                    cv2.circle(img, (int(lm.x * img.shape[1]), int(lm.y * img.shape[0])),
                               4, (0, 255, 0), -1)
            if not show(img):
                return False
            if elapsed >= COUNTDOWN_S + duration:
                break

    updates = {}
    if len(samples["zona"]) > 30:
        pts = np.array(samples["zona"])
        (x0, y0), (x1, y1) = np.percentile(pts, 3, axis=0), np.percentile(pts, 97, axis=0)
        # Un poco más chica que lo recorrido: así los bordes de pantalla se alcanzan
        # cómodos. Pero nunca menor que MIN_REGION: con una zona chiquita el cursor
        # se vuelve hipersensible.
        mx, my = (x1 - x0) * 0.05, (y1 - y0) * 0.05
        x0, x1, y0, y1 = x0 + mx, x1 - mx, y0 + my, y1 - my
        x0, x1 = _widen(x0, x1, MIN_REGION[0])
        y0, y1 = _widen(y0, y1, MIN_REGION[1])
        updates["region_x"] = [round(float(x0), 3), round(float(x1), 3)]
        updates["region_y"] = [round(float(y0), 3), round(float(y1), 3)]
    if len(samples["pinch"]) > 30:
        closed, opened = np.percentile(samples["pinch"], [10, 90])
        if opened - closed > 0.2:
            # Cerca del pellizco cerrado (no a mitad de camino a la mano abierta,
            # que daría clics con un pellizco a medias), y con tope.
            on = min(closed + 0.15 * (opened - closed), 0.35)
            off = min(max(closed + 0.30 * (opened - closed), on + 0.08), 0.50)
            updates["pinch_on"] = round(float(on), 3)
            updates["pinch_off"] = round(float(off), 3)

    if not updates:
        print("Calibración: no hubo datos suficientes, se mantienen los valores actuales.")
        return True
    for key, value in updates.items():
        setattr(cfg, key, tuple(value) if isinstance(value, list) else value)
    save_overrides(updates)
    print(f"Calibración guardada en {CONFIG_PATH}: {updates}")
    return True

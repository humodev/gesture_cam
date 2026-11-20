"""Detección de gestos a partir de los 21 landmarks de MediaPipe.

Las acciones son pellizcos del pulgar con otro dedo y una pose "V": son mucho
más robustos que contar dedos levantados ante rotaciones de la mano.
"""

import numpy as np

WRIST, THUMB_TIP = 0, 4
FINGER_TIPS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
FINGER_PIPS = {"index": 6, "middle": 10, "ring": 14, "pinky": 18}
PALM = [0, 5, 9, 17]  # muñeca + bases de índice, medio y meñique


def to_points(landmarks, aspect: float) -> np.ndarray:
    """Landmarks → array (21, 2) con x corregida por aspecto (distancias isotrópicas)."""
    return np.array([(lm.x * aspect, lm.y) for lm in landmarks], dtype=np.float32)


def hand_size(pts: np.ndarray) -> float:
    return float(np.linalg.norm(pts[9] - pts[WRIST])) or 1e-6


def palm_center(landmarks) -> tuple[float, float]:
    """Centro de la palma en coordenadas normalizadas de cámara. Se mueve poco
    al pellizcar, por eso se usa como puntero en lugar de la punta del índice."""
    xs = [landmarks[i].x for i in PALM]
    ys = [landmarks[i].y for i in PALM]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def pinch_ratios(pts: np.ndarray) -> dict[str, float]:
    size = hand_size(pts)
    return {name: float(np.linalg.norm(pts[tip] - pts[THUMB_TIP])) / size
            for name, tip in FINGER_TIPS.items()}


def finger_states(pts: np.ndarray) -> dict[str, str]:
    """"up" / "down" / "mid" por dedo, comparando distancias a la muñeca
    (independiente de la rotación de la mano)."""
    states = {}
    for name, tip in FINGER_TIPS.items():
        d_tip = np.linalg.norm(pts[tip] - pts[WRIST])
        d_pip = np.linalg.norm(pts[FINGER_PIPS[name]] - pts[WRIST])
        states[name] = "up" if d_tip > d_pip * 1.15 else "down" if d_tip < d_pip else "mid"
    return states


def is_v_pose(states: dict[str, str]) -> bool:
    return (states["index"] == "up" and states["middle"] == "up"
            and states["ring"] == "down" and states["pinky"] == "down")


def is_open_hand(states: dict[str, str]) -> bool:
    return all(s == "up" for s in states.values())


def curled_count(states: dict[str, str]) -> int:
    return sum(s == "down" for s in states.values())


def hand_pose(pts: np.ndarray, states: dict[str, str]) -> str | None:
    """"fist" (puño), "thumbs_up" (pulgar arriba) o None."""
    if curled_count(states) < 4:
        return None
    size = hand_size(pts)
    thumb_out = np.linalg.norm(pts[THUMB_TIP] - pts[5]) / size > 0.55
    thumb_up = pts[THUMB_TIP][1] < pts[2][1] - 0.35 * size  # y crece hacia abajo
    return "thumbs_up" if thumb_out and thumb_up else "fist"


class PinchDetector:
    """Un solo pellizco activo a la vez, con histéresis y confirmación por frames."""

    def __init__(self, on: float, off: float, confirm_frames: int):
        self.on, self.off, self.confirm = on, off, confirm_frames
        self.active: str | None = None
        self._candidate: str | None = None
        self._count = 0

    def reset(self) -> str | None:
        """Cancela el pellizco activo; devuelve cuál era (para soltarlo)."""
        ended, self.active = self.active, None
        self._candidate, self._count = None, 0
        return ended

    def progress(self, ratios: dict[str, float]) -> tuple[str, float]:
        """Dedo más cercano al pulgar y cuánto falta para el pellizco (0 = lejos, 1 = pellizco)."""
        name = self.active or min(ratios, key=ratios.get)
        span = max(self.off * 1.6 - self.on, 1e-6)
        return name, float(np.clip((self.off * 1.6 - ratios[name]) / span, 0.0, 1.0))

    def update(self, ratios: dict[str, float]) -> tuple[str | None, str | None]:
        """Devuelve (empezó, terminó): nombre del dedo o None."""
        if self.active:
            if ratios[self.active] > self.off:
                return None, self.reset()
            return None, None

        name = min(ratios, key=ratios.get)
        if ratios[name] >= self.on:
            self._candidate, self._count = None, 0
            return None, None
        if name != self._candidate:
            self._candidate, self._count = name, 0
        self._count += 1
        if self._count >= self.confirm:
            self.active = name
            self._candidate, self._count = None, 0
            return name, None
        return None, None


class Debounced:
    """Booleano que solo cambia tras `frames` lecturas seguidas iguales."""

    def __init__(self, frames: int):
        self.frames, self.value, self._count = frames, False, 0

    def update(self, raw: bool) -> bool:
        if raw == self.value:
            self._count = 0
        else:
            self._count += 1
            if self._count >= self.frames:
                self.value, self._count = raw, 0
        return self.value

    def reset(self) -> None:
        self.value, self._count = False, 0

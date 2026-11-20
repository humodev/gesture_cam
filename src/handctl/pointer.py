"""Cursor con ganancia según la velocidad de la mano.

Lento → el cursor se mueve una fracción de lo que se mueve la mano (apuntar
a cosas chicas). Rápido → 1:1 y además se va re-alineando con la posición
absoluta de la mano, así nunca se "pierde" la correspondencia mano-pantalla.
"""

import math
from collections import deque


class PrecisionCursor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pos: tuple[float, float] | None = None
        self.history: deque[tuple[float, float, float]] = deque(maxlen=30)
        self._prev: tuple[float, float] | None = None
        self._prev_t = 0.0

    def detach(self) -> None:
        """La mano que guía cambió o se perdió: no calcular delta con la anterior."""
        self._prev = None

    def update(self, target: tuple[float, float], t: float, frozen: bool = False):
        cfg = self.cfg
        if self.pos is None:
            self.pos = target
        if self._prev is not None and not frozen:
            dt = max(t - self._prev_t, 1e-3)
            dx, dy = target[0] - self._prev[0], target[1] - self._prev[1]
            speed = math.hypot(dx, dy) / dt
            s = min(max((speed - cfg.precision_slow) / (cfg.precision_fast - cfg.precision_slow), 0.0), 1.0)
            gain = cfg.precision_gain + (1 - cfg.precision_gain) * s
            x, y = self.pos[0] + dx * gain, self.pos[1] + dy * gain
            pull = 1 - math.exp(-cfg.drift_correction * s * dt)
            x += (target[0] - x) * pull
            y += (target[1] - y) * pull
            self.pos = (min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0))
        self._prev, self._prev_t = target, t
        self.history.append((t, *self.pos))
        return self.pos

    def rewind(self, seconds: float, now: float) -> tuple[float, float]:
        """Vuelve a donde estaba el cursor hace `seconds` (antes del pellizco)."""
        for t, x, y in reversed(self.history):
            if t <= now - seconds:
                self.pos = (x, y)
                break
        return self.pos

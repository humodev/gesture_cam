"""Contrato entre el host de gestos (controller + overlay) y las apps a pantalla
completa que se abren dentro de él (instrumentos, etc.).

Mientras una app está abierta, el host NO mueve el mouse ni escribe: le pasa
las manos en juego a la app y le presta el overlay para dibujar. El host se
reserva dos gestos, que las apps no deben usar:

  - 👍 pulgar arriba mantenido: pausa / reanudar (global).
  - ✊ puño mantenido: cerrar la app (y abrir/cerrar el lanzador de apps).
    Las poses cuentan solo con la mano quieta: si la palma se aleja más de
    cfg.pose_still de donde empezó la pose, el conteo vuelve a cero (un puño
    que va y viene en recorridos grandes no cierra la app).

Se abren desde el lanzador (✊ en modo mouse → "Apps"). Mientras el host está
en pausa la app sigue abierta pero `update` recibe `hands=[]` (soltar notas).
Si `open`, `update` o `paint` tiran una excepción, el host cierra la app
(llama a `close`) y vuelve al modo mouse.

Hilos:
  - update() corre en el hilo de control (~30 veces/s) con `ctl.lock` tomado.
  - paint() corre en el hilo de UI de Qt con `ctl.lock` tomado.
  - El audio corre en su propio hilo (callback de sounddevice): pasarle
    parámetros con asignaciones simples o colas, nunca bloquear.
"""

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class AppContext:
    cfg: object
    # Área de la app en coordenadas del widget overlay (px): x, y, ancho, alto.
    rect: tuple[int, int, int, int]
    # Coordenadas normalizadas de cámara (x, y de un landmark, imagen espejada)
    # → px del overlay dentro de `rect`, usando la zona activa calibrada
    # (cfg.region_x/region_y). Puede devolver puntos fuera de `rect`.
    to_screen: Callable[[float, float], tuple[float, float]]
    extras: dict = field(default_factory=dict)


class GestureApp:
    """Base de las apps. Las manos que recibe `update` son
    `handctl.controller.HandTrack` en juego; atributos estables que pueden usar:

      side            "left" | "right" (mano real del usuario)
      landmarks       21 landmarks de MediaPipe (x, y normalizados de cámara)
      palm            (x, y) centro de la palma, normalizado de cámara
      pos             (x, y) palma filtrada y mapeada a la zona activa, en [0, 1]
      pinch.active    "index" | "middle" | "ring" | "pinky" | None
      started/ended   pellizco que empezó/terminó en ESTE frame (o None)
      ratios          distancia pulgar-dedo / tamaño de mano, por dedo
      states          "up" | "down" | "mid" por dedo largo
      pose            "fist" | "thumbs_up" | None (reservadas para el host)
      v_pose.value    True si la mano está en V
      palm_velocity() (vx, vy) en anchos de cámara por segundo
    """

    name = "app"        # identificador corto, único
    title = "App"       # nombre visible en el lanzador
    icon = "🎵"         # emoji o 1-2 caracteres para el lanzador

    def open(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def close(self) -> None:
        """Liberar recursos y silenciar todo el audio."""

    def update(self, hands: list, now: float) -> None:
        """Procesar las manos en juego de este frame."""

    def paint(self, p, now: float) -> None:
        """Dibujar con el QPainter `p` dentro de self.ctx.rect."""

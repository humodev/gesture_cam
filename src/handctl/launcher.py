"""Lanzador: menú radial que se abre con ✊ puño mantenido.

Las opciones quedan en círculo alrededor del cursor. Para elegir: abrí la
mano, movela hacia la opción (se resalta) y pellizcá. La dirección se mide
como desplazamiento de la palma desde donde estaba al abrir la mano, así que
funciona igual en cualquier parte de la imagen y con cualquiera de las dos.

Contra disparos sin querer:
  - Para abrirlo hay que mantener el puño quieto ½ s (ver HandTrack).
  - Recién "se arma" cuando la mano está abierta y sin pellizco un instante:
    el pellizco que pueda formarse al abrir el puño no elige nada.
  - En el centro (zona muerta) no hay nada elegido: pellizcar ahí cierra el
    menú sin hacer nada (como hacer clic afuera).
  - Se elige la opción resaltada un instante ANTES del pellizco: pellizcar
    mueve un poco la palma y podría cambiar de sector justo al final.
  - Cambiar de sector tiene histéresis angular y la zona muerta también.
  - Se cierra con otro puño, pellizcando en el centro o solo tras unos segundos sin uso.
"""

import math
from collections import deque
from dataclasses import dataclass

# Opciones disponibles para `launcher_items` (config). kind: combo = atajo de
# teclado; el resto lo resuelve el controller.
CATALOG: dict[str, tuple[str, str, str, str]] = {
    # nombre: (título, ícono, kind, atajo)
    "copiar": ("Copiar", "📋", "combo", "ctrl+c"),
    "pegar": ("Pegar", "📥", "combo", "ctrl+v"),
    "deshacer": ("Deshacer", "↶", "combo", "ctrl+z"),
    "rehacer": ("Rehacer", "↷", "combo", "ctrl+shift+z"),
    "cerrar_pestana": ("Cerrar pestaña", "✕", "combo", "ctrl+w"),
    "nueva_pestana": ("Nueva pestaña", "＋", "combo", "ctrl+t"),
    "guardar": ("Guardar", "💾", "combo", "ctrl+s"),
    "enter": ("Enter", "⏎", "combo", "enter"),
    "escape": ("Escape", "⎋", "combo", "esc"),
    "play_pausa": ("Play / pausa", "⏯", "combo", "playpause"),
    "siguiente": ("Siguiente", "⏭", "combo", "nextsong"),
    "anterior": ("Anterior", "⏮", "combo", "previoussong"),
    "silencio": ("Silenciar", "🔇", "combo", "mute"),
    "vista_general": ("Vista general", "▦", "combo", "meta+w"),
    "escritorio_izq": ("Escritorio ←", "⇠", "combo", "ctrl+meta+left"),
    "escritorio_der": ("Escritorio →", "⇢", "combo", "ctrl+meta+right"),
    "volumen": ("Volumen", "🔊", "volume", ""),
    "teclado": ("Teclado", "⌨", "keyboard", ""),
    "dictar": ("Dictar", "🎤", "dictate", ""),
    "apps": ("Apps", "🎵", "apps", ""),
    "ayuda": ("Ayuda", "?", "help", ""),
}
VOLUME_KEYS = ("volumeup", "volumedown")


@dataclass
class Item:
    title: str
    icon: str
    kind: str          # combo | volume | keyboard | dictate | apps | app | help | back
    combo: str = ""
    app: type | None = None


def parse_item(name: str) -> Item:
    """Un nombre del catálogo o "Título=atajo" (p.ej. "Reabrir=ctrl+shift+t")."""
    if "=" in name:
        title, combo = (s.strip() for s in name.split("=", 1))
        return Item(title, "⌨", "combo", combo)
    if name not in CATALOG:
        raise ValueError(f"launcher_items: opción desconocida {name!r} "
                         f"(hay: {', '.join(CATALOG)}, o \"Título=atajo\")")
    return Item(*CATALOG[name])


def launcher_combos(cfg) -> list[str]:
    """Atajos que puede mandar el lanzador (para registrarlos en el teclado virtual)."""
    items = [parse_item(n) for n in cfg.launcher_items]
    return [i.combo for i in items if i.kind == "combo"] + list(VOLUME_KEYS)


class Launcher:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = [parse_item(n) for n in cfg.launcher_items]
        self.apps: list[type] = []      # clases de GestureApp disponibles
        self.can_dictate = False
        self.active = False
        self.page = "root"              # root | apps | volume | help
        self.items: list[Item] = []
        self.hand = None                # HandTrack que lo maneja
        self.armed = False
        self.anchor = (0.0, 0.0)        # palma (suavizada) al armarse
        self.offset = (0.0, 0.0)        # desplazamiento actual desde anchor
        self.hover: int | None = None
        self.chosen: int | None = None  # para el destello de confirmación
        self.chosen_at = -math.inf
        self.last_use = 0.0
        self.volume_level = 0           # pasos de volumen enviados en esta sesión
        self._smooth: tuple[float, float] | None = None
        self._calm_since: float | None = None
        self._hover_hist: deque[tuple[float, int | None]] = deque(maxlen=20)

    # ---------- abrir / cerrar ----------

    def visible_items(self) -> list[Item]:
        out = []
        for it in self.root:
            if it.kind == "apps" and not self.apps:
                continue  # sin apps registradas (o en --window) no se ofrece
            if it.kind == "dictate" and not self.can_dictate:
                continue
            out.append(it)
        return out

    def open(self, hand, now: float) -> None:
        self.active, self.hand = True, hand
        self._set_page("root", now)

    def close(self) -> None:
        self.active, self.hand, self.page, self.items = False, None, "root", []
        self.hover = None

    def _set_page(self, page: str, now: float) -> None:
        self.page = page
        if page == "root":
            self.items = self.visible_items()
        elif page == "apps":
            self.items = [Item(c.title, c.icon, "app", app=c) for c in self.apps]
            self.items.append(Item("Volver", "↩", "back"))
        else:
            self.items = []
        if page == "volume":
            self.volume_level = 0
        # Re-armar: la mano tiene que soltar el pellizco que eligió la opción
        # y quedarse quieta un instante; el centro pasa a ser la posición actual.
        self.armed, self.hover, self._calm_since = False, None, None
        self._hover_hist.clear()
        self.last_use = now

    # ---------- lógica (hilo de control) ----------

    def update(self, hands: list, now: float) -> list[Item]:
        """Procesa un frame. Devuelve las acciones a ejecutar (ya cerró si corresponde)."""
        cfg = self.cfg
        if self.hand not in hands:
            if not hands:  # sin manos en juego: espera (cierra por tiempo)
                return self._timeout(now)
            self.hand = hands[0]  # la otra mano toma el menú
            self._set_page(self.page, now)
        hand = self.hand
        aspect = cfg.cam_width / cfg.cam_height
        x, y = hand.palm[0] * aspect, hand.palm[1]
        if self._smooth is None or not self.armed:
            self._smooth = (x, y)
        else:  # suavizado leve: el resaltado no titila con el temblor de la mano
            sx, sy = self._smooth
            self._smooth = (sx + 0.5 * (x - sx), sy + 0.5 * (y - sy))

        if not self.armed:
            calm = hand.pose is None and hand.pinch.active is None
            if not calm:
                self._calm_since = None
            elif self._calm_since is None:
                self._calm_since = now
            elif now - self._calm_since >= cfg.launcher_arm_s:
                self.armed, self.anchor = True, self._smooth
                self.last_use = now
            return self._timeout(now)

        self.offset = (self._smooth[0] - self.anchor[0], self._smooth[1] - self.anchor[1])
        if self.page == "volume":
            return self._volume(hand, now)
        if self.page == "help":
            if hand.started:
                self.close()
                return []
            return self._timeout(now)

        prev = self.hover
        self.hover = self._sector(*self.offset)
        if self.hover != prev:
            self.last_use = now
        self._hover_hist.append((now, self.hover))
        if hand.started:
            pick = self._hover_before(now - cfg.click_lookback_s)
            if pick is None:
                self.close()
                return []
            return self._choose(pick, now)
        return self._timeout(now)

    def _timeout(self, now: float) -> list[Item]:
        if now - self.last_use > self.cfg.launcher_timeout_s:
            self.close()
        return []

    def _hover_before(self, t: float) -> int | None:
        for ts, h in reversed(self._hover_hist):
            if ts <= t:
                return h
        return self.hover

    def _sector(self, dx: float, dy: float) -> int | None:
        """Opción en la dirección del desplazamiento, o None en la zona muerta.
        La opción 0 está arriba y siguen en sentido horario."""
        n = len(self.items)
        dead = self.cfg.launcher_dead_zone
        r = math.hypot(dx, dy)
        if n == 0 or r < (dead * 0.75 if self.hover is not None else dead):
            return None
        width = 2 * math.pi / n
        ang = math.atan2(dx, -dy) % (2 * math.pi)
        idx = round(ang / width) % n
        if self.hover is not None and idx != self.hover:
            diff = abs((ang - self.hover * width + math.pi) % (2 * math.pi) - math.pi)
            if diff < width / 2 + math.radians(8):  # histéresis angular
                return self.hover
        return idx

    def _choose(self, idx: int, now: float) -> list[Item]:
        item = self.items[idx]
        self.chosen, self.chosen_at = idx, now
        if item.kind == "apps":
            self._set_page("apps", now)
            return []
        if item.kind == "back":
            self._set_page("root", now)
            return []
        if item.kind == "volume":
            self._set_page("volume", now)
            return []
        if item.kind == "help":
            self._set_page("help", now)
            return []
        self.close()
        return [item]

    def _volume(self, hand, now: float) -> list[Item]:
        """Subir/bajar la mano manda pasos de volumen; pellizco = listo."""
        if hand.started:
            self.close()
            return []
        level = int(-self.offset[1] / self.cfg.volume_step)
        out = []
        while level != self.volume_level:
            up = level > self.volume_level
            self.volume_level += 1 if up else -1
            out.append(Item("", "", "combo", VOLUME_KEYS[0] if up else VOLUME_KEYS[1]))
            self.last_use = now
        return out + self._timeout(now)

    def dot(self) -> tuple[float, float]:
        """Desplazamiento de la mano relativo a la zona muerta (1 = borde), para dibujar."""
        if not self.armed:
            return 0.0, 0.0
        d = self.cfg.launcher_dead_zone
        return self.offset[0] / d, self.offset[1] / d

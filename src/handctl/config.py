"""Parámetros ajustables.

Los valores por defecto viven acá; los tuyos (y los que escribe la
calibración) en ~/.config/handctl/config.toml, que pisa a estos.
"""

import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "handctl" / "config.toml"


@dataclass
class Config:
    # Cámara
    camera: int = 0
    cam_width: int = 1280
    cam_height: int = 720
    cam_fps: int = 30
    # Controles V4L2 aplicados con v4l2-ctl al abrir (se ignoran si fallan).
    # Sin framerate dinámico la cámara no baja a 15 FPS en penumbra;
    # 50 Hz evita parpadeo con la red eléctrica de Uruguay/Argentina.
    v4l2_controls: tuple[str, ...] = ("exposure_dynamic_framerate=0",
                                      "power_line_frequency=1")

    # Cualquier mano sola hace todo. Si aparecen las dos a la vez, esta toma el
    # cursor ("right" o "left"); la otra suma clics, scroll y gestos.
    dominant_hand: str = "right"
    # Una mano está "en juego" si su palma está dentro de esta zona de la imagen
    # (casi toda). Para descansar una mano: bajala al borde inferior o sacala.
    engage_zone_x: tuple[float, float] = (0.03, 0.97)
    engage_zone_y: tuple[float, float] = (0.03, 0.85)
    engage_s: float = 0.25      # tiempo dentro de la zona para entrar en juego
    disengage_s: float = 0.6    # tiempo fuera de la zona para pasar a reposo

    # Zona activa del frame (coordenadas normalizadas de cámara) que se
    # mapea a la pantalla completa. Más chica = menos movimiento de brazo.
    region_x: tuple[float, float] = (0.20, 0.80)
    region_y: tuple[float, float] = (0.15, 0.65)

    # Filtro One Euro (unidades: milésimas de pantalla)
    filter_min_cutoff: float = 1.0  # menos = más suave en reposo
    filter_beta: float = 0.01       # más = menos retraso en movimientos rápidos

    # Ganancia por velocidad: movimientos lentos mueven el cursor menos que la
    # mano (precisión) y los rápidos 1:1. La deriva se corrige sola al moverse rápido.
    precision_gain: float = 0.35     # ganancia mínima (mano lenta)
    precision_slow: float = 0.08     # velocidad (pantallas/s) con ganancia mínima
    precision_fast: float = 0.60     # velocidad con ganancia 1
    drift_correction: float = 6.0    # qué tan rápido se re-alinean mano y cursor

    # Pellizco: distancia pulgar-dedo / tamaño de mano (muñeca→base dedo medio)
    pinch_on: float = 0.28
    pinch_off: float = 0.40
    pinch_confirm_frames: int = 2

    # Con una sola mano, al empezar un pellizco se usa la posición de hace
    # `click_lookback_s` y se congela el cursor `click_freeze_s`.
    click_lookback_s: float = 0.10
    click_freeze_s: float = 0.25

    # Poses mantenidas: puño = abrir/cerrar teclado; pulgar arriba = pausa.
    mode_hold_s: float = 0.5
    pause_hold_s: float = 0.8

    # Scroll con la mano en "V" (índice y medio extendidos)
    scroll_speed: float = 4800.0     # unidades hi-res (120 = 1 paso) por unidad de mano
    scroll_invert: bool = False
    scroll_inertia_s: float = 0.35   # 0 = sin inercia

    # Gestos de sistema: deslizar la mano secundaria abierta (solo con 2 manos).
    swipe_speed: float = 1.8         # anchos de cámara por segundo
    swipe_cooldown_s: float = 0.8
    swipe_left: str = "ctrl+meta+left"    # escritorio anterior
    swipe_right: str = "ctrl+meta+right"  # escritorio siguiente
    swipe_up: str = "meta+w"              # vista general
    swipe_down: str = "meta+d"            # mostrar escritorio

    # Teclado: auto-repetición al mantener el pellizco en ⌫ / flechas
    repeat_delay_s: float = 0.45
    repeat_interval_s: float = 0.08
    # Margen (fracción de tecla) que hay que salir de una tecla para cambiar de hover
    key_stickiness: float = 0.2

    # Dictado (pellizco con el anular mantenido = grabar)
    voice_enabled: bool = True       # o --no-voice
    voice_model: str = "large-v3-turbo"
    voice_language: str = "es"
    voice_device: str = "cuda"       # "cpu" si no hay GPU
    # Tecla para pegar el texto dictado. En terminales: "ctrl+shift+v".
    voice_paste: str = "ctrl+v"

    # Segundos sin mano antes de soltar botones y resetear filtros
    lost_hand_s: float = 0.3

    # Overlay de pantalla (capa transparente encima de todo)
    overlay_screen: str = ""           # nombre de monitor (p.ej. "HDMI-A-1"); vacío = principal
    overlay_keyboard_width: float = 0.70   # fracción del ancho de pantalla
    overlay_keyboard_height: float = 0.40  # fracción del alto
    overlay_opacity: float = 0.85
    overlay_preview_width: int = 360   # cámara en miniatura; 0 = ocultar

    # Ventana de vista previa (modo --window y calibración en ventana)
    preview_width: int = 960


def load_config(path: Path = CONFIG_PATH) -> Config:
    cfg = Config()
    if not path.exists():
        return cfg
    known = {f.name for f in fields(Config)}
    for key, value in tomllib.loads(path.read_text()).items():
        if key not in known:
            print(f"config.toml: clave desconocida '{key}' (ignorada)")
            continue
        setattr(cfg, key, tuple(value) if isinstance(value, list) else value)
    return cfg


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(round(v, 4))
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(v)


def save_overrides(updates: dict, path: Path = CONFIG_PATH) -> None:
    """Agrega/actualiza claves en el config.toml del usuario sin tocar las demás."""
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    data.update(updates)
    defaults = asdict(Config())
    lines = ["# handctl: valores que pisan los de src/handctl/config.py"]
    lines += [f"{k} = {_toml_value(data[k])}" for k in sorted(data) if k in defaults]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")

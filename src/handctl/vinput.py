"""Mouse y teclado virtuales: elige el backend del sistema operativo.

Todos exponen la misma interfaz:
  VirtualMouse:    move(x, y) en [0, 1] del escritorio completo, button(btn, down),
                   click(btn), scroll_hires(unidades; 120 = un paso), release_all(),
                   close(), pressed (set de botones apretados)
  VirtualKeyboard: VirtualKeyboard(keycodes), tap(code, modifiers), combo("primary+c"), close()
Los códigos de tecla/botón son los de keycodes.py.
"""

from .keycodes import IS_LINUX, IS_MAC, IS_WINDOWS, parse_combo  # noqa: F401

if IS_LINUX:
    from .vinput_linux import VirtualKeyboard, VirtualMouse, settle
elif IS_WINDOWS:
    from .vinput_windows import VirtualKeyboard, VirtualMouse, settle
elif IS_MAC:
    from .vinput_mac import VirtualKeyboard, VirtualMouse, settle
else:
    raise ImportError("Sistema operativo no soportado")

__all__ = ["VirtualKeyboard", "VirtualMouse", "parse_combo", "settle"]

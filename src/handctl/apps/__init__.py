"""Registro de apps que el lanzador de gestos puede abrir. Ver base.py."""

import importlib
import importlib.util

from .base import AppContext, GestureApp

APPS: list[type[GestureApp]] = []


def register(cls: type[GestureApp]) -> type[GestureApp]:
    APPS.append(cls)
    return cls


def load_apps() -> list[type[GestureApp]]:
    """Importa los módulos que registran apps (lo llama el host al arrancar)."""
    if not APPS and importlib.util.find_spec(f"{__name__}.instruments"):
        importlib.import_module(f"{__name__}.instruments")  # se registran al importarse
    return APPS


__all__ = ["APPS", "AppContext", "GestureApp", "load_apps", "register"]

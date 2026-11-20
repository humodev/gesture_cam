"""Sonidos de percusión y golpes sintetizados (sin samples). Cada función
devuelve un buffer mono float32 a amplitud ~1; el octapad lo escala por la
fuerza del golpe."""

from functools import lru_cache

import numpy as np
from scipy.signal import lfilter

from ...audio import SR, karplus_strong, midi_freq, soft_saw

RNG = np.random.default_rng(7)  # mismo "ruido" cada vez: el kit suena igual siempre


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(SR * seconds)) / SR


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * cutoff / SR)
    return lfilter([1 - a], [1, -a], x)


def _highpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    return x - _lowpass(x, cutoff)


def _bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return _lowpass(_highpass(x, lo), hi)


def _env(t: np.ndarray, decay: float, attack: float = 0.001) -> np.ndarray:
    return np.minimum(t / attack, 1.0) * np.exp(-t / decay)


def _sweep(t: np.ndarray, f0: float, f1: float, speed: float) -> np.ndarray:
    """Seno cuya frecuencia cae de f0 a f1 (parche de tambor)."""
    f = f1 + (f0 - f1) * np.exp(-t * speed)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _norm(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    return x / (np.abs(x).max() + 1e-9)


def _noise(n: int) -> np.ndarray:
    return RNG.uniform(-1, 1, n)


# ---------------------------------------------------------------- batería

def kick() -> np.ndarray:
    t = _t(0.45)
    body = _sweep(t, 150, 48, 35) * _env(t, 0.22)
    click = _highpass(_noise(len(t)), 2000) * _env(t, 0.004)
    return _norm(np.tanh(1.6 * body) + 0.25 * click)


def snare() -> np.ndarray:
    t = _t(0.3)
    tone = _sweep(t, 260, 185, 40) * _env(t, 0.06)
    noise = _bandpass(_noise(len(t)), 1200, 7000) * _env(t, 0.13)
    return _norm(0.55 * tone + noise)


def hihat(open_: bool = False) -> np.ndarray:
    t = _t(0.5 if open_ else 0.08)
    metal = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (3140, 4170, 5310, 6700, 8120))
    x = _highpass(0.6 * metal + _noise(len(t)), 7000) * _env(t, 0.22 if open_ else 0.025)
    return _norm(x) * 0.7


def tom(f: float) -> np.ndarray:
    t = _t(0.5)
    return _norm(_sweep(t, f * 1.6, f, 18) * _env(t, 0.22) + 0.1 * _lowpass(_noise(len(t)), 900) * _env(t, 0.03))


def crash() -> np.ndarray:
    t = _t(1.8)
    metal = sum(np.sin(2 * np.pi * f * t + RNG.uniform(0, 6)) for f in (3200, 4100, 5900, 7300, 8900, 10100))
    x = _highpass(_noise(len(t)) + 0.3 * metal, 4000) * _env(t, 0.65, 0.002)
    return _norm(x) * 0.75


def clap() -> np.ndarray:
    t = _t(0.35)
    burst = np.zeros(len(t))
    for d in (0.0, 0.011, 0.022):  # tres palmadas casi juntas
        burst += np.exp(-np.maximum(t - d, 0) / 0.005) * (t >= d)
    tail = np.exp(-np.maximum(t - 0.03, 0) / 0.09) * (t >= 0.03) * 0.6
    return _norm(_bandpass(_noise(len(t)), 900, 5000) * (burst + tail))


def rim() -> np.ndarray:
    t = _t(0.08)
    return _norm(_sweep(t, 1900, 1600, 60) * _env(t, 0.012) + 0.4 * _highpass(_noise(len(t)), 3000) * _env(t, 0.004))


def cowbell() -> np.ndarray:
    t = _t(0.4)
    sq = np.sign(np.sin(2 * np.pi * 545 * t)) + np.sign(np.sin(2 * np.pi * 815 * t))
    return _norm(_bandpass(sq, 400, 3000) * _env(t, 0.11)) * 0.8


# ---------------------------------------------------------------- electrónica

def kick808() -> np.ndarray:
    t = _t(1.0)
    return _norm(np.tanh(1.3 * _sweep(t, 110, 52, 22) * _env(t, 0.45)))


def snare_elec() -> np.ndarray:
    t = _t(0.25)
    return _norm(0.4 * _sweep(t, 330, 220, 50) * _env(t, 0.04) + _highpass(_noise(len(t)), 2500) * _env(t, 0.09))


def zap() -> np.ndarray:
    t = _t(0.25)
    return _norm(_sweep(t, 2400, 120, 28) * _env(t, 0.08))


# ---------------------------------------------------------------- percusión

def conga(f: float) -> np.ndarray:
    t = _t(0.45)
    return _norm(_sweep(t, f * 1.25, f, 60) * _env(t, 0.16) + 0.15 * np.sin(2 * np.pi * f * 2.3 * t) * _env(t, 0.05))


def shaker() -> np.ndarray:
    t = _t(0.16)
    return _norm(_bandpass(_noise(len(t)), 4000, 11000) * _env(t, 0.035, attack=0.02)) * 0.7


def claves() -> np.ndarray:
    t = _t(0.15)
    return _norm(np.sin(2 * np.pi * 2500 * t) * _env(t, 0.03))


def tambourine() -> np.ndarray:
    t = _t(0.35)
    jingles = sum(np.sin(2 * np.pi * f * t) for f in (5200, 6900, 8800))
    return _norm(_highpass(_noise(len(t)) + 0.5 * jingles, 5000) * _env(t, 0.09)) * 0.7


def timbale(f: float) -> np.ndarray:
    t = _t(0.6)
    ring = np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * f * 1.52 * t)
    return _norm(ring * _env(t, 0.2) + 0.5 * _highpass(_noise(len(t)), 2000) * _env(t, 0.01))


# ---------------------------------------------------------------- melódicos

def marimba(midi: int) -> np.ndarray:
    t = _t(1.2)
    f = midi_freq(midi)
    x = np.sin(2 * np.pi * f * t) * _env(t, 0.45) + 0.35 * np.sin(2 * np.pi * f * 3.93 * t) * _env(t, 0.06)
    return _norm(x)


def chord_stab(root: int, intervals) -> np.ndarray:
    t = _t(1.4)
    x = np.zeros(len(t))
    for m in [root] + [root + 12 + i for i in intervals]:
        f = midi_freq(m)
        x += soft_saw(2 * np.pi * f * t) + soft_saw(2 * np.pi * f * 1.004 * t)
    return _norm(_lowpass(x, 2600) * _env(t, 0.5, attack=0.008)) * 0.8


def pluck(midi: int) -> np.ndarray:
    return karplus_strong(midi_freq(midi), amp=1.0, t60=2.0, brightness=0.6)


# ---------------------------------------------------------------- kits

MAJ, MIN, DOM7 = (0, 4, 7), (0, 3, 7), (0, 4, 7, 10)

# Cada kit: 8 pads (fila de arriba primero), (etiqueta, generador).
KITS: dict[str, list[tuple[str, object]]] = {
    "Batería": [("Platillo", crash), ("Tom agudo", lambda: tom(200)), ("Tom grave", lambda: tom(120)),
                ("Hi-hat abierto", lambda: hihat(True)),
                ("Bombo", kick), ("Caja", snare), ("Hi-hat", hihat), ("Aplauso", clap)],
    "Electrónica": [("Zap", zap), ("Cencerro", cowbell), ("Tom 808", lambda: tom(90)),
                    ("Hat abierto", lambda: hihat(True)),
                    ("Bombo 808", kick808), ("Caja", snare_elec), ("Hat", hihat), ("Clap", clap)],
    "Percusión": [("Timbal", lambda: timbale(420)), ("Pandereta", tambourine), ("Claves", claves),
                  ("Cencerro", cowbell),
                  ("Conga baja", lambda: conga(190)), ("Conga alta", lambda: conga(285)),
                  ("Bongó", lambda: conga(420)), ("Shaker", shaker)],
    "Marimba": [(n, (lambda m=m: marimba(m))) for n, m in
                (("Do5", 72), ("Re5", 74), ("Mi5", 76), ("Sol5", 79),
                 ("Do4", 60), ("Re4", 62), ("Mi4", 64), ("Sol4", 67))],
    "Acordes": [(n, (lambda r=r, i=i: chord_stab(r, i))) for n, r, i in
                (("Rem", 50, MIN), ("Mim", 52, MIN), ("Mi", 52, MAJ), ("Sol7", 43, DOM7),
                 ("Do", 48, MAJ), ("Sol", 43, MAJ), ("Lam", 45, MIN), ("Fa", 41, MAJ))],
    "Guitarra": [(n, (lambda m=m: pluck(m))) for n, m in
                 (("La4", 69), ("Do5", 72), ("Re5", 74), ("Mi5", 76),
                  ("Mi3", 52), ("La3", 57), ("Re4", 62), ("Sol4", 67))],
}


@lru_cache(maxsize=None)
def kit_buffers(name: str) -> tuple[np.ndarray, ...]:
    """Buffers de un kit (se calculan la primera vez y quedan en memoria)."""
    return tuple((gen() * 0.9).astype(np.float32) for _, gen in KITS[name])

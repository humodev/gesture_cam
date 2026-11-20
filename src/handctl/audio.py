"""Motor de audio propio: síntesis en tiempo real con numpy + sounddevice.

Un único stream de salida mezcla "voces". Cada voz genera bloques de audio en
el hilo de audio (`render`), así que su trabajo es vectorizado y corto; lo
pesado (p.ej. precalcular una cuerda) se hace al crear la voz, en el hilo
de control. Los parámetros se cambian con asignaciones simples y las voces los
interpolan por bloque para que no haya "clics".
"""

import threading

import numpy as np
import sounddevice as sd
from scipy.signal import lfilter

SR = 48000
BLOCK = 256  # ~5 ms
NOTE_NAMES = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]
MAJOR = [0, 2, 4, 5, 7, 9, 11]
PENTATONIC = [0, 2, 4, 7, 9]


def midi_freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def freq_midi(freq: float) -> float:
    return 69 + 12 * np.log2(max(freq, 1e-6) / 440.0)


def note_name(midi: float) -> str:
    m = int(round(midi))
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def snap_to_scale(midi: float, scale=MAJOR, root: int = 0) -> int:
    """Nota de la escala más cercana."""
    best = None
    for octave in range(int(midi // 12) - 1, int(midi // 12) + 2):
        for step in scale:
            cand = octave * 12 + root + step
            if best is None or abs(cand - midi) < abs(best - midi):
                best = cand
    return best


def soft_saw(phase: np.ndarray, harmonics: int = 6) -> np.ndarray:
    """Diente de sierra suave (pocos armónicos): sin aliasing y no tan áspero."""
    out = np.zeros_like(phase)
    for k in range(1, harmonics + 1):
        out += np.sin(k * phase) / k
    return out * 0.55


class Voice:
    pan = 0.0     # -1 izquierda … 1 derecha
    done = False  # el motor la descarta cuando es True

    def render(self, n: int) -> np.ndarray:
        raise NotImplementedError


# ---------------------------------------------------------------- voces

class ThereminVoice(Voice):
    """Oscilador continuo con glissando, vibrato y volumen suavizados."""

    def __init__(self, glide_s: float = 0.04):
        self.freq = self.target_freq = 440.0
        self.amp = self.target_amp = 0.0
        self.glide_s = glide_s
        self._phase = 0.0
        self._vib = 0.0

    def set(self, freq: float, amp: float) -> None:
        self.target_freq, self.target_amp = freq, amp

    def render(self, n: int) -> np.ndarray:
        k = min(1.0, n / (SR * self.glide_s))
        f1 = self.freq * (self.target_freq / self.freq) ** k
        a1 = self.amp + (self.target_amp - self.amp) * min(1.0, n / (SR * 0.025))
        freqs = np.geomspace(self.freq, f1, n)
        t = (self._vib + np.arange(n)) / SR
        freqs = freqs * (1 + 0.005 * np.sin(2 * np.pi * 5.5 * t))
        phase = self._phase + np.cumsum(2 * np.pi * freqs / SR)
        self._phase = float(phase[-1] % (2 * np.pi))
        self._vib += n
        amps = np.linspace(self.amp, a1, n)
        self.freq, self.amp = f1, a1
        wave = np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.08 * np.sin(3 * phase)
        return (wave * amps * 0.5).astype(np.float32)


def karplus_strong(freq: float, amp: float = 0.8, t60: float = 2.5,
                   brightness: float = 0.6) -> np.ndarray:
    """Cuerda pulsada precalculada (Karplus-Strong), vectorizado por períodos."""
    n_period = max(int(SR / freq), 2)
    total = int(SR * min(t60, 4.0))
    # Excitación: ruido, más suave cuanto menos brillo (pulsar con la yema).
    noise = np.random.uniform(-1, 1, n_period)
    smooth = max(1, int((1 - brightness) * 8))
    noise = np.convolve(noise, np.ones(smooth) / smooth, mode="same")
    damping = 0.001 ** (1 / (freq * t60))  # cae 60 dB en t60 segundos
    out = np.zeros(total + n_period + 1)
    out[1:n_period + 1] = noise
    pos = n_period + 1
    while pos < len(out):
        m = min(n_period, len(out) - pos)
        out[pos:pos + m] = damping * 0.5 * (out[pos - n_period:pos - n_period + m]
                                            + out[pos - n_period - 1:pos - n_period - 1 + m])
        pos += m
    sig = out[n_period + 1:]
    sig -= sig.mean()
    fade = np.ones_like(sig)
    fade[-2000:] = np.linspace(1, 0, 2000)
    return (sig * fade * amp / (np.abs(sig).max() + 1e-9)).astype(np.float32)


class SampleVoice(Voice):
    """Reproduce un buffer precalculado (cuerdas, golpes)."""

    def __init__(self, samples: np.ndarray, pan: float = 0.0):
        self.samples, self.pan, self._pos = samples, pan, 0

    def render(self, n: int) -> np.ndarray:
        chunk = self.samples[self._pos:self._pos + n]
        self._pos += n
        if len(chunk) < n:
            self.done = True
            chunk = np.pad(chunk, (0, n - len(chunk)))
        return chunk


class PadVoice(Voice):
    """Nota sostenida (colchón): 3 osciladores desafinados, envolvente y filtro."""

    def __init__(self, freq: float, amp: float = 0.25, attack: float = 0.12,
                 release: float = 0.8, cutoff: float = 2500.0, pan: float = 0.0):
        self.freqs = np.array([freq, freq * 1.004, freq * 0.996])
        self.amp, self.attack, self.release, self.pan = amp, attack, release, pan
        self.cutoff = cutoff
        self._phase = np.random.uniform(0, 2 * np.pi, 3)
        self._env = 0.0
        self._releasing = False
        self._zi = np.zeros(1)

    def stop(self) -> None:
        self._releasing = True

    def render(self, n: int) -> np.ndarray:
        steps = np.arange(1, n + 1)
        if self._releasing:
            env = np.maximum(self._env - steps / (self.release * SR), 0.0)
        else:
            env = np.minimum(self._env + steps / (self.attack * SR), 1.0)
        self._env = float(env[-1])
        wave = np.zeros(n)
        for i, f in enumerate(self.freqs):
            ph = self._phase[i] + np.cumsum(np.full(n, 2 * np.pi * f / SR))
            self._phase[i] = ph[-1] % (2 * np.pi)
            wave += soft_saw(ph)
        a = np.exp(-2 * np.pi * self.cutoff / SR)  # filtro pasa-bajos de un polo
        wave, self._zi = lfilter([1 - a], [1, -a], wave, zi=self._zi)
        if self._releasing and self._env <= 0:
            self.done = True
        return (wave * env * self.amp / 3).astype(np.float32)


class ArpVoice(Voice):
    """Arpegiador con reloj propio en el hilo de audio (ritmo exacto, no
    depende de los 30 FPS de la cámara). Se controla con atributos:
    notes (lista MIDI), rate (notas/s), brightness (0-1), gate (on/off)."""

    def __init__(self):
        self.notes: list[int] = [60, 64, 67, 72]
        self.rate = 6.0
        self.brightness = 0.5
        self.volume = 0.5
        self.gate = False
        self._countdown = 0  # muestras hasta la próxima nota
        self._t = 0          # muestras renderizadas desde que existe la voz
        self._step = 0
        self._playing: list[tuple[np.ndarray, int]] = []  # (buffer, muestra de inicio)

    def _make_note(self, midi: int) -> np.ndarray:
        f = midi_freq(midi)
        dur = int(SR * 0.4)
        t = np.arange(dur) / SR
        wave = soft_saw(2 * np.pi * f * t, harmonics=8)
        env = np.exp(-t * 7) * np.minimum(t / 0.004, 1)
        cutoff = 300 + self.brightness ** 2 * 6000
        a = np.exp(-2 * np.pi * cutoff / SR)
        wave = lfilter([1 - a], [1, -a], wave)
        wave = lfilter([1 - a], [1, -a], wave)  # dos polos: más "sinte"
        return (wave * env * self.volume).astype(np.float32)

    def render(self, n: int) -> np.ndarray:
        if self.gate:
            while self._countdown < n:  # notas que arrancan dentro de este bloque
                notes = self.notes or [60]
                self._playing.append((self._make_note(notes[self._step % len(notes)]),
                                      self._t + self._countdown))
                self._step += 1
                self._countdown += int(SR / max(self.rate, 0.5))
            self._countdown -= n
        else:
            self._countdown = 0  # al volver a activar, la primera nota suena ya
        out = np.zeros(n, np.float32)
        for buf, start in self._playing:
            a = max(start - self._t, 0)
            src = self._t + a - start
            chunk = buf[src:src + n - a]
            out[a:a + len(chunk)] += chunk
        self._t += n
        self._playing = [(b, s) for b, s in self._playing if self._t - s < len(b)]
        return out


# ---------------------------------------------------------------- reverb

class Reverb:
    """Reverb de Schroeder liviana (4 peines + 2 pasa-todo). Todos los retardos
    son > BLOCK, así cada bloque se procesa vectorizado contra el pasado."""

    def __init__(self, mix: float = 0.18):
        self.mix = mix
        self.combs = [(d, 0.78) for d in (1557, 1617, 1491, 1422)]
        self.allpasses = [(556, 0.5), (341, 0.5)]
        size = 4096
        self._comb_buf = [np.zeros(size) for _ in self.combs]
        self._ap_in = [np.zeros(size) for _ in self.allpasses]
        self._ap_out = [np.zeros(size) for _ in self.allpasses]

    @staticmethod
    def _push(buf: np.ndarray, data: np.ndarray) -> None:
        buf[:-len(data)] = buf[len(data):]
        buf[-len(data):] = data

    def __call__(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        wet = np.zeros(n)
        for (d, g), buf in zip(self.combs, self._comb_buf):
            y = x + g * buf[len(buf) - d:len(buf) - d + n]
            self._push(buf, y)
            wet += y
        wet *= 0.25
        for (d, g), bin_, bout in zip(self.allpasses, self._ap_in, self._ap_out):
            y = -g * wet + bin_[len(bin_) - d:len(bin_) - d + n] + g * bout[len(bout) - d:len(bout) - d + n]
            self._push(bin_, wet)
            self._push(bout, y)
            wet = y
        return x * (1 - self.mix) + wet * self.mix


# ---------------------------------------------------------------- motor

class Engine:
    def __init__(self):
        self.voices: list[Voice] = []
        self.master = 0.8
        self.reverb = Reverb()
        self._lock = threading.Lock()
        self._stream = None

    def start(self) -> None:
        if self._stream is None:
            self._stream = sd.OutputStream(samplerate=SR, blocksize=BLOCK, channels=2,
                                           dtype="float32", latency="low", callback=self._callback)
            self._stream.start()

    def add(self, voice: Voice) -> Voice:
        with self._lock:
            self.voices.append(voice)
        return voice

    def remove(self, voice: Voice) -> None:
        with self._lock:
            if voice in self.voices:
                self.voices.remove(voice)

    def clear(self) -> None:
        with self._lock:
            self.voices.clear()

    def stop(self) -> None:
        self.clear()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, out, frames, _time, _status) -> None:
        with self._lock:
            voices = list(self.voices)
        left = np.zeros(frames)
        right = np.zeros(frames)
        for v in voices:
            try:
                s = v.render(frames)
            except Exception:
                v.done = True
                continue
            pl, pr = np.sqrt((1 - v.pan) / 2), np.sqrt((1 + v.pan) / 2)
            left += s * pl
            right += s * pr
        if any(v.done for v in voices):
            with self._lock:
                self.voices = [v for v in self.voices if not v.done]
        mid = self.reverb((left + right) * 0.5)
        out[:, 0] = np.tanh((left * 0.6 + mid * 0.4) * self.master)
        out[:, 1] = np.tanh((right * 0.6 + mid * 0.4) * self.master)


_engine: Engine | None = None


def get_engine() -> Engine:
    """Motor compartido por todas las apps (se abre la primera vez)."""
    global _engine
    if _engine is None:
        _engine = Engine()
    _engine.start()
    return _engine

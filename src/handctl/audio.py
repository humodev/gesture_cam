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


# ---------------------------------------------------------------- looper

def _click(freq: float, amp: float) -> np.ndarray:
    t = np.arange(int(SR * 0.03)) / SR
    return (np.sin(2 * np.pi * freq * t) * np.exp(-t * 180) * amp).astype(np.float32)


class Looper:
    """Looper de audio sincronizado a un tempo. Graba lo que suenan las voces
    (en seco, sin el propio bucle) en capas de un largo fijo de compases y las
    reproduce en bucle. Corre dentro del callback del motor.

    Desde el hilo de control solo se llaman los métodos públicos; el estado
    que cruza hilos son asignaciones simples."""

    BEATS_PER_BAR = 4
    MAX_LAYERS = 16  # cada capa ocupa hasta ~7 MB (4 compases a 50 BPM)

    def __init__(self):
        self.bpm = 90
        self.bars = 2
        self.metronome = True
        self.volume = 0.9
        self.layers: list[np.ndarray] = []
        self.mix: np.ndarray | None = None   # suma de las capas (L, 2)
        self.playing = False
        self.pos = 0                          # posición en el bucle (muestras)
        self.countin = 0                      # muestras de cuenta previa restantes
        self.armed = False                    # grabar en la próxima vuelta
        self.recording: np.ndarray | None = None
        self._clicks: list[list] = []         # [buffer, posición]
        self._accent, self._beat = _click(1760, 0.5), _click(1180, 0.32)

    # ---------- medidas ----------

    @property
    def beat_len(self) -> int:
        return int(SR * 60 / self.bpm)

    @property
    def length(self) -> int:
        return self.beat_len * self.BEATS_PER_BAR * self.bars

    def beat_now(self) -> float:
        """Pulso actual (con fracción) dentro del bucle, para dibujar."""
        if self.countin > 0:
            return -self.countin / self.beat_len
        return self.pos / self.beat_len

    @property
    def state(self) -> str:
        if self.countin > 0:
            return "cuenta"
        if self.recording is not None:
            return "grabando"
        if self.armed:
            return "armado"
        return "sonando" if self.playing else "parado"

    # ---------- control (hilo de control) ----------

    def set_tempo(self, bpm: int) -> None:
        if not self.layers and self.recording is None:  # con capas grabadas no se puede
            self.bpm = int(min(max(bpm, 50), 180))

    def record(self) -> None:
        """Botón grabar: arma una capa nueva; con el bucle vacío, cuenta 1 compás.
        Mientras graba, vuelve a tocarlo para cancelar esa capa."""
        if len(self.layers) >= self.MAX_LAYERS and self.recording is None and not self.armed:
            return
        if self.recording is not None or self.armed or self.countin > 0:
            self.recording, self.armed, self.countin = None, False, 0
            if not self.layers:
                self.playing = False
            return
        if not self.playing:
            self.pos = 0
            self.countin = self.beat_len * self.BEATS_PER_BAR
            self.playing = True
        self.armed = True

    def toggle_play(self) -> None:
        if self.playing:
            self.playing, self.armed, self.recording, self.countin = False, False, None, 0
        elif self.layers:
            self.pos, self.playing = 0, True

    def undo(self) -> None:
        if self.recording is not None or self.armed:
            self.recording, self.armed = None, False
            return
        if self.layers:
            last = self.layers.pop()
            self.mix = self.mix - last if self.layers else None
            if not self.layers:
                self.playing = False

    def clear(self) -> None:
        self.playing, self.armed, self.recording, self.countin = False, False, None, 0
        self.layers, self.mix = [], None

    # ---------- audio (hilo de audio) ----------

    def _schedule_clicks(self, a: int, m: int, offset: int) -> None:
        """Clics de metrónomo que caen en [a, a+m) de la línea de tiempo."""
        bl = self.beat_len
        k = -(-a // bl)  # primer pulso >= a
        while k * bl < a + m:
            accent = k % self.BEATS_PER_BAR == 0
            self._clicks.append([self._accent if accent else self._beat, -(offset + k * bl - a)])
            k += 1

    def process(self, live_l: np.ndarray, live_r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n = len(live_l)
        out = np.zeros((n, 2))
        i = 0
        while i < n and self.playing:
            if self.countin > 0:  # cuenta previa: solo metrónomo
                m = min(self.countin, n - i)
                total = self.beat_len * self.BEATS_PER_BAR
                self._schedule_clicks(total - self.countin, m, i)
                self.countin -= m
                i += m
                continue
            if self.pos == 0 and self.armed:
                self.recording, self.armed = np.zeros((self.length, 2), np.float32), False
            m = min(self.length - self.pos, n - i)
            if self.metronome or self.recording is not None and not self.layers:
                self._schedule_clicks(self.pos, m, i)
            if self.mix is not None:
                out[i:i + m] += self.mix[self.pos:self.pos + m] * self.volume
            rec = self.recording
            if rec is not None:
                rec[self.pos:self.pos + m, 0] = live_l[i:i + m]
                rec[self.pos:self.pos + m, 1] = live_r[i:i + m]
            self.pos += m
            i += m
            if self.pos >= self.length:
                self.pos = 0
                if rec is not None and self.recording is rec:
                    self.layers.append(rec)
                    self.mix = rec.copy() if self.mix is None else self.mix + rec
                    self.recording = None
        # Clics (pueden cruzar bloques).
        for c in self._clicks:
            buf, p = c
            start = max(-p, 0)
            src = max(p, 0)
            chunk = buf[src:src + n - start]
            out[start:start + len(chunk), 0] += chunk
            out[start:start + len(chunk), 1] += chunk
            c[1] = p + n
        self._clicks = [c for c in self._clicks if c[1] < len(c[0])]
        return out[:, 0], out[:, 1]


# ---------------------------------------------------------------- motor

class Engine:
    def __init__(self):
        self.voices: list[Voice] = []
        self.master = 0.8
        self.reverb = Reverb()
        self.looper = Looper()  # persiste entre apps: el bucle sigue sonando
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
        # El looper graba en seco lo que suenan las voces y le suma el bucle.
        loop_l, loop_r = self.looper.process(left, right)
        left += loop_l
        right += loop_r
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

"""Dictado por voz con Whisper local (faster-whisper).

Pulsar para hablar: se graba mientras dura el pellizco, al soltar se
transcribe en segundo plano y el texto se pega en la ventana con foco.
Se pega vía el portapapeles de KDE (Klipper) porque el teclado virtual solo
puede mandar teclas del layout US: así llegan tildes, ñ, ¿ y ¡.
"""

import ctypes
import glob
import os
import shutil
import site
import subprocess
import threading
import time

import numpy as np

SAMPLE_RATE = 16000
# Frases que Whisper "alucina" sobre silencio o ruido.
HALLUCINATIONS = ("gracias por ver", "subtítulos", "suscríbete", "amara.org", "gracias.")


def _preload_cuda_libs() -> None:
    """faster-whisper busca cuBLAS/cuDNN en el sistema; acá vienen de los
    paquetes pip nvidia-*, así que se cargan a mano antes de importarlo."""
    for sp in site.getsitepackages():
        for pattern in ("nvidia/cublas/lib/libcublas*.so*", "nvidia/cudnn/lib/libcudnn*.so*"):
            for lib in sorted(glob.glob(os.path.join(sp, pattern))):
                try:
                    ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


class Klipper:
    def __init__(self):
        self.qdbus = shutil.which("qdbus6") or shutil.which("qdbus")

    def _call(self, *args: str) -> str | None:
        if not self.qdbus:
            return None
        r = subprocess.run([self.qdbus, "org.kde.klipper", "/klipper", *args],
                           capture_output=True, text=True)
        return r.stdout.removesuffix("\n") if r.returncode == 0 else None

    def get(self) -> str | None:
        return self._call("getClipboardContents")

    def set(self, text: str) -> bool:
        return self._call("setClipboardContents", text) is not None


class Dictation:
    def __init__(self, cfg, kbd, paste: bool = True):
        self.cfg, self.kbd, self.paste = cfg, kbd, paste
        self.state = "loading"  # loading | ready | recording | transcribing | error
        self.message = "cargando Whisper..."
        self.level = 0.0
        self.model = None
        self.clipboard = Klipper()
        self._chunks: list[np.ndarray] = []
        self._stream = None
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            _preload_cuda_libs()
            from faster_whisper import WhisperModel
            device = self.cfg.voice_device
            try:
                self.model = WhisperModel(self.cfg.voice_model, device=device,
                                          compute_type="float16" if device == "cuda" else "int8")
            except Exception as ex:  # sin GPU / sin CUDA: modelo chico en CPU
                print(f"Whisper en {device} falló ({ex}); usando 'small' en CPU.")
                self.model = WhisperModel("small", device="cpu", compute_type="int8")
            self.state, self.message = "ready", ""
        except Exception as ex:
            self.state, self.message = "error", f"dictado no disponible: {ex}"
            print(self.message)

    def _on_audio(self, indata, _frames, _time, _status) -> None:
        self._chunks.append(indata[:, 0].copy())
        self.level = float(np.sqrt(np.mean(indata[:, 0] ** 2)))

    def start(self) -> None:
        if self.state != "ready":
            return
        import sounddevice as sd
        self._chunks = []
        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                          dtype="float32", callback=self._on_audio)
            self._stream.start()
        except Exception as ex:
            self.message = f"micrófono: {ex}"
            return
        self.state, self.message = "recording", ""

    def stop(self) -> None:
        if self.state != "recording":
            return
        self._stream.stop()
        self._stream.close()
        self._stream, self.level = None, 0.0
        audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
        self.state = "transcribing"
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio: np.ndarray) -> None:
        try:
            if len(audio) < SAMPLE_RATE * 0.3 or np.sqrt(np.mean(audio ** 2)) < 0.002:
                self.message = "(no se escuchó nada)"
                return
            segments, _ = self.model.transcribe(
                audio, language=self.cfg.voice_language, vad_filter=True,
                condition_on_previous_text=False, beam_size=5)
            text = "".join(s.text for s in segments).strip()
            if not text or (len(text) < 40 and any(h in text.lower() for h in HALLUCINATIONS)):
                self.message = "(no se entendió)"
                return
            if self.paste and not self._paste(text + " "):
                self.message = "Klipper no responde"
            else:
                self.message = f"«{text}»"
        except Exception as ex:
            self.message = f"error: {ex}"
        finally:
            self.state = "ready"

    def _paste(self, text: str) -> bool:
        previous = self.clipboard.get()
        if not self.clipboard.set(text):
            return False
        time.sleep(0.05)
        self.kbd.combo(self.cfg.voice_paste)
        if previous:  # devolver el portapapeles como estaba
            time.sleep(0.4)
            self.clipboard.set(previous)
        return True

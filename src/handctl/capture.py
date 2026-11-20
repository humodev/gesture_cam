"""Cámara + MediaPipe: entrega frames espejados con los landmarks de las manos."""

import shutil
import sys
import subprocess
import time
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")


def ensure_model(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Descargando modelo en {path} ...")
        urllib.request.urlretrieve(MODEL_URL, path)


# Backend de captura nativo de cada sistema (DirectShow acepta MJPG en Windows).
CAMERA_BACKEND = (cv2.CAP_DSHOW if sys.platform == "win32" else
                  cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_V4L2)


def open_camera(cfg) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(cfg.camera, CAMERA_BACKEND)
    # MJPG: en YUYV muchas webcams USB no llegan a 30 FPS.
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.cam_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.cam_height)
    cap.set(cv2.CAP_PROP_FPS, cfg.cam_fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        raise SystemExit(f"No se pudo abrir la cámara {cfg.camera}")
    if cfg.v4l2_controls and shutil.which("v4l2-ctl"):
        for ctrl in cfg.v4l2_controls:
            subprocess.run(["v4l2-ctl", "-d", f"/dev/video{cfg.camera}", "-c", ctrl],
                           capture_output=True)
    return cap


class HandCapture:
    """Itera (now, frame_espejado, resultado). Con la imagen espejada, la
    lateralidad que reporta MediaPipe coincide con la mano real del usuario."""

    def __init__(self, cfg, model: Path = MODEL_PATH, num_hands: int = 2):
        ensure_model(model)
        self.landmarker = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO, num_hands=num_hands,
            min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5))
        self.cap = open_camera(cfg)
        # MediaPipe exige timestamps crecientes aunque se itere varias veces
        # (calibración y después control).
        self._t0, self._last_ms = time.monotonic(), -1

    def __iter__(self):
        while True:
            ok, frame = self.cap.read()
            if not ok:
                print("La cámara dejó de entregar frames.")
                return
            now = time.monotonic()
            frame = cv2.flip(frame, 1)
            self._last_ms = max(int((now - self._t0) * 1000), self._last_ms + 1)
            img = mp.Image(image_format=mp.ImageFormat.SRGB,
                           data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            yield now, frame, self.landmarker.detect_for_video(img, self._last_ms)

    def close(self) -> None:
        self.cap.release()
        self.landmarker.close()

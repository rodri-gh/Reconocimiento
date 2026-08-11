"""Gestión de cámaras: workers por cámara + prueba de conexión RTSP/MJPEG/snapshot.

En la Fase 1 el worker mantiene el stream abierto y reporta estado/last_seen.
En la Fase 2 se conecta `process_frame` (YOLO + ByteTrack) como hook.
"""
from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np

from backend.core.crypto import decrypt_secret

logger = logging.getLogger("camera_manager")

RTSP = "rtsp"
MJPG = "mjpeg"
SNAP = "snapshot"
SUPPORTED_STREAM_TYPES = (RTSP, MJPG, SNAP)

FRAME_TIMEOUT_S = 10.0  # si no llega frame en X segundos -> reconectar


def detect_stream_type(url: str) -> str:
    lowered = url.strip().lower()
    if lowered.startswith("rtsp://") or lowered.startswith("rtsps://"):
        return RTSP
    if lowered.startswith("http://") or lowered.startswith("https://"):
        if "snapshot" in lowered or lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
            return SNAP
        return MJPG
    raise ValueError(f"URL no soportada (usa rtsp:// o http://): {url}")


def build_effective_url(rtsp_url: str, username: str = "", password_encrypted: str = "") -> str:
    """Inyecta user:pass en la URL si fueron configurados."""
    url = rtsp_url.strip()
    if not username and not password_encrypted:
        return url
    parts = urlsplit(url)
    if parts.username:
        return url  # la URL ya trae credenciales; se respeta tal cual
    password = decrypt_secret(password_encrypted) if password_encrypted else ""
    userinfo = username or ""
    if password:
        userinfo = f"{userinfo}:{password}" if userinfo else password
    if not userinfo:
        return url
    return urlunsplit((parts.scheme, f"{userinfo}@{parts.netloc}", parts.path, parts.query, parts.fragment))


def _open_capture(effective_url: str) -> cv2.VideoCapture | None:
    cap = cv2.VideoCapture(effective_url)
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
    return cap


def test_connection(camera: dict) -> dict:
    """Abre la cámara, lee frames y reporta si es accesible."""
    effective_url = build_effective_url(
        camera.get("rtsp_url", ""),
        camera.get("username", ""),
        camera.get("password_encrypted", ""),
    )
    cap = _open_capture(effective_url)
    if cap is None or not cap.isOpened():
        return {"ok": False, "error": "No se pudo conectar a la URL de la cámara", "url": effective_url}

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    backend = cap.getBackendName() if hasattr(cap, "getBackendName") else ""

    frames_read = 0
    last_error = ""
    deadline = time.time() + FRAME_TIMEOUT_S
    while time.time() < deadline and frames_read < 5:
        ret, frame = cap.read()
        if ret and frame is not None:
            frames_read += 1
            if not width:
                height, width = frame.shape[:2]
        else:
            last_error = "El stream no entrega frames"
            time.sleep(0.3)
    cap.release()

    if frames_read == 0:
        return {"ok": False, "error": last_error or "No se recibieron frames del stream", "url": effective_url}

    return {
        "ok": True,
        "url": effective_url,
        "stream_type": detect_stream_type(camera.get("rtsp_url", "")),
        "width": width,
        "height": height,
        "fps": fps,
        "backend": backend,
        "frames_read": frames_read,
    }


class FrameGrabber(threading.Thread):
    """Hilo secundario que drena continuamente el buffer de OpenCV para evitar lag."""

    def __init__(self, cap, stop_event: threading.Event) -> None:
        super().__init__(daemon=True, name="frame-grabber")
        self.cap = cap
        self.stop_event = stop_event
        self.latest_frame = None
        self.new_frame_event = threading.Event()
        self.lock = threading.Lock()

    def run(self) -> None:
        while not self.stop_event.is_set():
            ret, frame = self.cap.read()
            if ret and frame is not None:
                with self.lock:
                    self.latest_frame = frame
                self.new_frame_event.set()
            else:
                time.sleep(0.05)

    def get_latest_frame(self):
        with self.lock:
            frame = self.latest_frame
            self.latest_frame = None
        return frame


class CameraWorker(threading.Thread):
    """Hilo que mantiene el stream de una cámara y (en Fase 2) procesa frames."""

    def __init__(self, camera: dict, process_frame=None, process_every: int = 3) -> None:
        super().__init__(daemon=True, name=f"camera-{camera['id']}")
        self.camera = camera
        self.process_frame = process_frame  # callable(frame) -> None (hook Fase 2)
        self.process_every = process_every
        self._stop = threading.Event()

        self.status: str = "idle"
        self.last_seen: float = 0.0
        self.last_error: str = ""
        self.frame_count: int = 0
        self.width: int = 0
        self.height: int = 0
        self._frame_lock = threading.Lock()
        self._latest_jpeg: bytes = b""

    @property
    def effective_url(self) -> str:
        return build_effective_url(
            self.camera.get("rtsp_url", ""),
            self.camera.get("username", ""),
            self.camera.get("password_encrypted", ""),
        )

    def stop(self) -> None:
        self._stop.set()

    def state(self) -> dict:
        return {
            "camera_id": self.camera["id"],
            "status": self.status,
            "last_seen": self.last_seen,
            "last_error": self.last_error,
            "frame_count": self.frame_count,
            "width": self.width,
            "height": self.height,
            "has_live_frame": bool(self._latest_jpeg),
        }

    def latest_jpeg(self) -> bytes:
        with self._frame_lock:
            return self._latest_jpeg

    def _set_latest_frame(self, frame) -> None:
        ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            with self._frame_lock:
                self._latest_jpeg = buffer.tobytes()

    def run(self) -> None:
        retry_delay = 5.0
        while not self._stop.is_set():
            self.status = "connecting"
            self.last_error = ""
            cap = _open_capture(self.effective_url)
            if cap is None or not cap.isOpened():
                self.status = "error"
                self.last_error = "No se pudo conectar al stream"
                logger.warning("[%s] conexión fallida", self.camera["name"])
                self._sleep_retry(retry_delay)
                continue

            self.status = "running"
            self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            logger.info("[%s] stream activo (%dx%d)", self.camera["name"], self.width, self.height)

            grabber_stop = threading.Event()
            grabber = FrameGrabber(cap, grabber_stop)
            grabber.start()

            stalled_since: float | None = None
            try:
                while not self._stop.is_set():
                    grabber.new_frame_event.wait(timeout=0.1)
                    grabber.new_frame_event.clear()

                    frame = grabber.get_latest_frame()
                    if frame is not None:
                        self.frame_count += 1
                        self.last_seen = time.time()
                        stalled_since = None
                        processed = False
                        if self.process_frame:
                            try:
                                annotated = self.process_frame(frame)
                                if isinstance(annotated, bytes):
                                    with self._frame_lock:
                                        self._latest_jpeg = annotated
                                    processed = True
                            except Exception:
                                logger.exception("[%s] error en process_frame", self.camera["name"])
                        if not processed:
                            self._set_latest_frame(frame)
                    else:
                        if stalled_since is None:
                            stalled_since = time.time()
                        elif time.time() - stalled_since > FRAME_TIMEOUT_S:
                            self.status = "offline"
                            self.last_error = "Sin frames del stream"
                            break
            finally:
                grabber_stop.set()
                grabber.join(timeout=2)
                cap.release()

            if self._stop.is_set():
                break
            self.status = "offline"
            self._sleep_retry(retry_delay)

    def _sleep_retry(self, delay: float) -> None:
        self._stop.wait(delay)


class CameraManager:
    """Registry de workers activos por id de cámara."""

    def __init__(self) -> None:
        self._workers: dict[str, CameraWorker] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._sync_callback = None
        self._sync_stop = threading.Event()
        self._sync_thread: threading.Thread | None = None

    def start_sync_loop(self, on_state, interval: float = 5.0) -> None:
        """Sincroniza periódicamente el estado de los workers hacia PocketBase."""
        self._sync_callback = on_state
        self._sync_stop.clear()
        self._sync_thread = threading.Thread(target=self._sync_loop, args=(interval,), daemon=True, name="state-sync")
        self._sync_thread.start()

    def _sync_loop(self, interval: float) -> None:
        while not self._sync_stop.is_set():
            time.sleep(interval)
            if not self._sync_callback:
                continue
            for camera_id, worker in list(self._workers.items()):
                try:
                    self._sync_callback(camera_id, worker.state())
                except Exception:
                    logger.exception("Sync de estado de cámara %s falló", camera_id)

    def start(self, camera: dict, process_frame=None, process_every: int = 3, auto_stop_delay: float = 600.0) -> None:
        camera_id = camera["id"]
        with self._lock:
            # Cancelar temporizador anterior si existía
            old_timer = self._timers.pop(camera_id, None)
            if old_timer:
                old_timer.cancel()

            old = self._workers.get(camera_id)
            if old and old.is_alive():
                old.stop()
                old.join(timeout=3)
            worker = CameraWorker(camera, process_frame=process_frame, process_every=process_every)
            self._workers[camera_id] = worker
            worker.start()

            # Programar la detención automática después de 10 minutos (600s)
            if auto_stop_delay > 0:
                def _auto_stop_callback():
                    logger.info("[%s] Detención automática ejecutada después de %s segundos", camera.get("name"), auto_stop_delay)
                    self.stop(camera_id)
                    if self._sync_callback:
                        # Forzar actualización de estado a PocketBase
                        try:
                            self._sync_callback(camera_id, {
                                "status": "idle",
                                "last_seen": time.time(),
                                "last_error": "Detenido automáticamente después de 10 minutos"
                            })
                        except Exception:
                            logger.exception("Fallo al forzar sync tras auto-stop de cámara %s", camera_id)

                t = threading.Timer(auto_stop_delay, _auto_stop_callback)
                t.daemon = True
                self._timers[camera_id] = t
                t.start()

    def stop(self, camera_id: str) -> None:
        with self._lock:
            # Cancelar el temporizador asociado
            timer = self._timers.pop(camera_id, None)
            if timer:
                timer.cancel()
            worker = self._workers.pop(camera_id, None)
        if worker:
            worker.stop()
            worker.join(timeout=3)

    def restart(self, camera: dict, process_frame=None) -> None:
        self.stop(camera["id"])
        self.start(camera, process_frame=process_frame)

    def is_running(self, camera_id: str) -> bool:
        worker = self._workers.get(camera_id)
        return bool(worker and worker.is_alive())

    def status(self, camera_id: str) -> dict | None:
        worker = self._workers.get(camera_id)
        return worker.state() if worker else None

    def latest_jpeg(self, camera_id: str) -> bytes | None:
        worker = self._workers.get(camera_id)
        return worker.latest_jpeg() if worker else None

    def statuses(self) -> dict[str, dict]:
        return {cid: w.state() for cid, w in self._workers.items()}

    def stop_all(self) -> None:
        self._sync_stop.set()
        with self._lock:
            # Cancelar todos los temporizadores activos
            for camera_id, timer in list(self._timers.items()):
                timer.cancel()
            self._timers.clear()
        for camera_id in list(self._workers.keys()):
            self.stop(camera_id)


manager = CameraManager()

"""Servidor MJPEG local de prueba.

Sirve una "cámara" simulada. Puede repetir una imagen (--image), reproducir un
video en bucle (--video) o generar frames sintéticos.

Uso:
    python backend/tools/mjpeg_server.py
    python backend/tools/mjpeg_server.py --image bus.jpg
    python backend/tools/mjpeg_server.py --video trafico.mp4
"""
from __future__ import annotations

import argparse
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np

JPEG_QUALITY = 85


def _encode(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return buf.tobytes() if ok else b""


def synthetic_frame(t: float) -> bytes:
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.rectangle(img, (0, 0), (639, 359), (40, 40, 40), -1)
    x = int((t * 80) % 560) + 40
    cv2.circle(img, (x, 180), 30, (0, 128, 255), -1)
    cv2.putText(img, "CAMARA DEMO LOCAL", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    cv2.putText(img, f"frame t={t:0.2f}", (20, 320), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1)
    return _encode(img)


def image_frame(img: np.ndarray, t: float) -> bytes:
    # Pequeño panning para que las detecciones varíen levemente entre frames.
    shift = int((t * 40) % 80)
    rolled = np.roll(img, shift, axis=1)
    return _encode(rolled)


def make_handler(source=None, video_path=None):
    """Devuelve un handler para una fuente de imagen o video."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/video.mjpg"):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                t0 = time.time()
                capture = cv2.VideoCapture(video_path) if video_path else None
                try:
                    while True:
                        if capture is not None:
                            ok, image = capture.read()
                            if not ok:
                                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                                continue
                            frame = _encode(image)
                        else:
                            frame = source(time.time() - t0)
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    if capture is not None:
                        capture.release()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):  # noqa: D401
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=str, default=None, help="ruta a una imagen para usarla como stream")
    parser.add_argument("--video", type=str, default=None, help="ruta a un video para reproducirlo en bucle")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    if args.image and args.video:
        print("Usa solo --image o --video, no ambos")
        return 1
    if args.video:
        capture = cv2.VideoCapture(args.video)
        if not capture.isOpened():
            print(f"No se pudo abrir el video: {args.video}")
            return 1
        capture.release()
        print(f"Serving video en bucle: {args.video}")
        source = None
    elif args.image:
        img = cv2.imread(args.image)
        if img is None:
            print(f"No se pudo leer la imagen: {args.image}")
            return 1
        print(f"Serving imagen: {args.image} ({img.shape[1]}x{img.shape[0]})")
        source = lambda t: image_frame(img, t)  # noqa: E731
    else:
        print("Serving frames sinteticos")
        source = synthetic_frame

    print(f"MJPEG server en http://127.0.0.1:{args.port}/video.mjpg")
    HTTPServer(("127.0.0.1", args.port), make_handler(source, args.video)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

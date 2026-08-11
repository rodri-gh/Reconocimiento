"""Descarga imágenes nuevas de autos, las procesa y registra eventos en PB."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import cv2
import requests

from backend.core.config import settings
from backend.services.detector import get_detector
from backend.services.event_pipeline import save_detection
from backend.services.ocr_plate import get_plate_reader
from backend.services.pocketbase_client import PocketBaseClient

logging.basicConfig(level=logging.INFO)

VEHICLES = {"Auto", "Moto", "Bus", "Camion"}


def get_test_camera(pb: PocketBaseClient) -> dict:
    for camera in pb.list_cameras():
        if camera.get("name") == "Pruebas nuevas - imágenes de autos":
            return camera
    return pb.create_camera(
        {
            "name": "Pruebas nuevas - imágenes de autos",
            "rtsp_url": "https://loremflickr.com/960/640/car,vehicle",
            "username": "",
            "password_encrypted": "",
            "stream_type": "snapshot",
            "status": "idle",
            "enabled": False,
        }
    )


def main() -> None:
    pb = PocketBaseClient(
        settings.pocketbase_url,
        settings.pocketbase_admin_email,
        settings.pocketbase_admin_password,
    )
    pb.authenticate()
    pb.ensure_collections()
    camera = get_test_camera(pb)
    detector = get_detector()
    reader = get_plate_reader()
    registered = 0
    downloaded = 0

    with tempfile.TemporaryDirectory(prefix="new-car-tests-") as temp_dir:
        for lock in range(100, 116):
            url = f"https://loremflickr.com/960/640/car,vehicle?lock={lock}"
            path = Path(temp_dir) / f"car_{lock}.jpg"
            try:
                response = requests.get(url, headers={"User-Agent": "Reconocimiento-demo/1.0"}, timeout=45)
                response.raise_for_status()
                path.write_bytes(response.content)
                frame = cv2.imread(str(path))
                if frame is None:
                    continue
                downloaded += 1
                detections = [d for d in detector.process_frame(frame) if d["class_name"] in VEHICLES]
                logging.info("imagen nueva lock=%s | vehículos=%s", lock, len(detections))

                for d in detections:
                    x1, y1, x2, y2 = d["xyxy"]
                    crop = frame[y1:y2, x1:x2]
                    plate_text = ""
                    plate_bbox = None
                    if d["class_name"] in VEHICLES:
                        plate_text, local_bbox = reader.read_plate_details(crop)
                        if local_bbox:
                            plate_bbox = (
                                x1 + local_bbox[0],
                                y1 + local_bbox[1],
                                x1 + local_bbox[2],
                                y1 + local_bbox[3],
                            )
                    save_detection(pb, camera, frame, d, plate_text, plate_bbox)
                    registered += 1

            except Exception as exc:  # keep trying the remaining fresh images
                logging.warning("No se pudo procesar lock=%s: %s", lock, exc)

    print(f"Imágenes nuevas descargadas: {downloaded}")
    print(f"Eventos nuevos registrados en PocketBase: {registered}")
    print(f"Cámara de prueba: {camera['id']} ({camera['name']})")


if __name__ == "__main__":
    main()

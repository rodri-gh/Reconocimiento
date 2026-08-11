"""Reprocesa imágenes de detecciones guardadas en PocketBase, sin modificar DB."""
from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import requests

from backend.core.config import settings
from backend.services.detector import get_detector
from backend.services.ocr_plate import PlateReader
from backend.services.pocketbase_client import PocketBaseClient, PocketBaseError


def main() -> None:
    pb = PocketBaseClient(
        settings.pocketbase_url,
        settings.pocketbase_admin_email,
        settings.pocketbase_admin_password,
    )
    pb.authenticate()
    records = pb.list_detections({"perPage": 200, "sort": "-detected_at"})
    vehicle_records = [r for r in records if r.get("object_type") in {"Auto", "Moto", "Bus", "Camion"}]
    detector = get_detector()
    reader = PlateReader(enabled=settings.enable_ocr, plate_model_path=settings.plate_model)

    print(f"PocketBase: {settings.pocketbase_url}")
    print(f"Imágenes totales: {len(records)} | vehículos: {len(vehicle_records)}")

    with tempfile.TemporaryDirectory(prefix="plate-db-") as temp_dir:
        for record in vehicle_records:
            image_url = pb.file_url(settings.pocketbase_url, record, "image")
            try:
                response = requests.get(image_url, timeout=30)
                response.raise_for_status()
                path = Path(temp_dir) / f"{record['id']}.jpg"
                path.write_bytes(response.content)
                frame = cv2.imread(str(path))
                if frame is None:
                    raise ValueError("imagen inválida")

                detections = detector.process_frame(frame)
                vehicle_detections = [
                    d for d in detections if d["class_name"] in {"Auto", "Moto", "Bus", "Camion"}
                ]
                results = []
                for detection in vehicle_detections:
                    x1, y1, x2, y2 = detection["xyxy"]
                    crop = frame[y1:y2, x1:x2]
                    plate = reader.read_plate(crop)
                    results.append(f"{detection['class_name']}={plate}")
                full_frame_plate = reader.read_plate(frame)

                print(
                    f"{record['id']} | guardado={record.get('object_type')}:{record.get('plate_text')} "
                    f"| recorte={', '.join(results) or 'sin vehículo'} | frame={full_frame_plate}"
                )
            except (requests.RequestException, OSError, ValueError, PocketBaseError) as exc:
                print(f"{record['id']} | ERROR: {exc}")


if __name__ == "__main__":
    main()

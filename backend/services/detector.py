"""Detector YOLO + ByteTrack. Analiza frames y devuelve personas/vehículos con tracker_id.

El debounce de captura vive en event_pipeline.py; aquí solo se hace detección + tracking.
"""
from __future__ import annotations

import logging
import threading

import numpy as np
import supervision as sv

logger = logging.getLogger("detector")

# Clases COCO: 0=persona, 2=auto, 3=moto, 5=bus, 7=camión
TARGET_CLASSES = np.array([0, 2, 3, 5, 7], dtype=int)
VEHICLE_CLASS_IDS = {2, 3, 5, 7}
CLASS_NAMES = {0: "Persona", 2: "Auto", 3: "Moto", 5: "Bus", 7: "Camion"}
DEFAULT_CLASS_NAME = "Otro"


class Detector:
    def __init__(self, model_path: str = "yolov8n.pt", confidence: float = 0.5) -> None:
        self.model_path = model_path
        self.confidence = confidence
        self._model = None
        self._lock = threading.Lock()
        self.tracker = sv.ByteTrack()

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from ultralytics import YOLO
                    import torch

                    # Limitar hilos en CPU para evitar sobrecarga del procesador
                    torch.set_num_threads(1)

                    logger.info("Cargando modelo YOLO: %s", self.model_path)
                    self._model = YOLO(self.model_path)
        return self._model

    def process_frame(self, frame: np.ndarray) -> list[dict]:
        """Devuelve detecciones: [{xyxy, confidence, class_id, class_name, tracker_id}]."""
        model = self._get_model()
        results = model(frame, verbose=False)[0]
        detections = sv.Detections.from_ultralytics(results)

        out: list[dict] = []
        if len(detections) == 0:
            return out

        mask = np.isin(detections.class_id, TARGET_CLASSES)
        detections = detections[mask]
        if len(detections) == 0:
            return out

        # YOLO ocasionalmente labels the same vehicle as both car and truck.
        # Keep the highest-confidence vehicle box when they overlap heavily.
        detections = self._suppress_duplicate_vehicles(detections)

        detections = self.tracker.update_with_detections(detections)

        for xyxy, conf, class_id, tracker_id in zip(
            detections.xyxy, detections.confidence, detections.class_id, detections.tracker_id
        ):
            if tracker_id is None:
                continue
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            cid = int(class_id)
            out.append(
                {
                    "xyxy": (x1, y1, x2, y2),
                    "confidence": float(conf),
                    "class_id": cid,
                    "class_name": CLASS_NAMES.get(cid, DEFAULT_CLASS_NAME),
                    "tracker_id": int(tracker_id),
                }
            )
        return out

    @staticmethod
    def _suppress_duplicate_vehicles(detections):
        if len(detections) < 2:
            return detections
        boxes = detections.xyxy
        classes = detections.class_id
        confidences = detections.confidence
        order = sorted(range(len(detections)), key=lambda i: float(confidences[i]), reverse=True)
        kept: list[int] = []
        for index in order:
            class_id = int(classes[index])
            duplicate = False
            if class_id in VEHICLE_CLASS_IDS:
                for other in kept:
                    if int(classes[other]) not in VEHICLE_CLASS_IDS:
                        continue
                    if Detector._iou(boxes[index], boxes[other]) >= 0.65 or Detector._containment(boxes[index], boxes[other]) >= 0.8:
                        duplicate = True
                        break
            if not duplicate:
                kept.append(index)
        return detections[kept]

    @staticmethod
    def _iou(a, b) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - intersection
        return float(intersection / union) if union else 0.0

    @staticmethod
    def _containment(a, b) -> float:
        """Porcentaje del cuadro menor cubierto por el mayor."""
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        smaller = min(area_a, area_b)
        return float(intersection / smaller) if smaller else 0.0


_detector_singleton: Detector | None = None
_detector_lock = threading.Lock()


def get_detector() -> Detector:
    global _detector_singleton
    if _detector_singleton is None:
        with _detector_lock:
            if _detector_singleton is None:
                from backend.core.config import settings

                _detector_singleton = Detector(
                    model_path=getattr(settings, "yolo_model", "yolov8n.pt"),
                    confidence=getattr(settings, "confidence_threshold", 0.5),
                )
    return _detector_singleton

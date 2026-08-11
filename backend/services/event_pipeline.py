"""Pipeline de eventos: detección + debounce por tracker_id + captura a PocketBase.

Cada cámara activa tiene un `process_frame` construido aquí. La primera vez que
aparece un tracker_id nuevo con confianza suficiente se dispara un evento
(foto + registro). Mientras ese mismo ID siga en pantalla no se repite.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import cv2

from backend.core.config import settings
from backend.services.detector import get_detector
from backend.services.ocr_plate import get_plate_reader

logger = logging.getLogger("event_pipeline")

VEHICLE_CLASSES = {"Auto", "Moto", "Bus", "Camion"}
JPEG_QUALITY = 85
MAX_CAPTURED_IDS = 1000


def _draw_detection(annotated, d: dict, plate_bbox: tuple[int, int, int, int] | None = None, plate_text: str = "") -> None:
    x1, y1, x2, y2 = d["xyxy"]
    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
    label = f"{d['class_name']} #{d['tracker_id']} {d['confidence']:.2f}"
    cv2.putText(annotated, label, (x1, max(15, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    if plate_bbox:
        px1, py1, px2, py2 = plate_bbox
        cv2.rectangle(annotated, (px1, py1), (px2, py2), (0, 215, 255), 3)
        if plate_text:
            cv2.putText(annotated, plate_text, (px1, max(18, py1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 215, 255), 2)


def _annotate(frame, d: dict, plate_bbox: tuple[int, int, int, int] | None = None, plate_text: str = "") -> bytes:
    annotated = frame.copy()
    _draw_detection(annotated, d, plate_bbox, plate_text)
    ok, buf = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return buf.tobytes() if ok else b""


def save_detection(pb, camera_record: dict, track: dict) -> None:
    """Corre el OCR en el mejor cuadro acumulado y sube el evento a PocketBase."""
    try:
        from backend.services.ocr_plate import get_plate_reader
        plate_reader = get_plate_reader()
        
        d = track["best_yolo_detection"]
        frame = track["best_frame"]
        
        # Correr OCR sobre el mejor recorte de vehículo (el de mayor área)
        plate_text = "SIN_PLACA_DETECTADA"
        plate_bbox = None
        
        if d["class_name"] in VEHICLE_CLASSES:
            x1, y1, x2, y2 = d["xyxy"]
            x1, y1 = max(0, x1), max(0, y1)
            y2, x2 = min(frame.shape[0], y2), min(frame.shape[1], x2)
            crop = frame[y1:y2, x1:x2]
            
            if crop.size > 0:
                text, local_bbox, ocr_prob = plate_reader.read_plate_details(crop)
                if text and text != "SIN_PLACA_DETECTADA":
                    plate_text = text
                    if local_bbox:
                        lx1, ly1, lx2, ly2 = local_bbox
                        plate_bbox = (x1 + lx1, y1 + ly1, x1 + lx2, y1 + ly2)

        img_bytes = _annotate(frame, d, plate_bbox, plate_text)
        filename = f"{d['class_name'].lower()}_id{d['tracker_id']}_{uuid.uuid4().hex[:8]}.jpg"
        detected_at = datetime.now(timezone.utc).isoformat()
        data = {
            "camera": camera_record["id"],
            "object_type": d["class_name"],
            "plate_text": plate_text,
            "confidence": round(d["confidence"], 3),
            "tracker_id": d["tracker_id"],
            "detected_at": detected_at,
        }
        record = pb.create_record_with_file("detections", data, "image", img_bytes, filename)
        logger.info(
            "EVENTO capturado | camara=%s | tipo=%s | tracker=%s | placa=%s | record=%s | area_max=%d",
            camera_record.get("name"),
            d["class_name"],
            d["tracker_id"],
            plate_text,
            record.get("id"),
            track["best_area"]
        )
    except Exception:
        logger.exception("Fallo al guardar evento de la cámara")


def build_process_frame(pb, camera_record: dict) -> callable:
    """Construye el hook process_frame(frame) para el worker de una cámara."""
    detector = get_detector()

    captured: set[int] = set()
    last_event_class: dict[int, float] = {}
    last_detections = []
    
    # Acumulador de tracking: tracker_id -> dict
    active_tracks: dict[int, dict] = {}
    
    frame_idx = 0
    lock = threading.Lock()
    uploader = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"upload-{camera_record['id']}")

    detecting = False
    detecting_lock = threading.Lock()

    def process_frame(frame) -> bytes | None:
        nonlocal captured, last_event_class, last_detections, frame_idx, detecting, active_tracks
        frame_idx += 1

        # Verificar si la cámara fue detenida
        worker_thread_alive = any(t.name == f"camera-{camera_record['id']}" and t.is_alive() for t in threading.enumerate())
        if not worker_thread_alive:
            # Procesar y subir todo lo que quedó pendiente en active_tracks hasta este momento
            for tid in list(active_tracks.keys()):
                track = active_tracks[tid]
                if not track["uploaded"]:
                    track["uploaded"] = True
                    with lock:
                        captured.add(tid)
                    uploader.submit(save_detection, pb, camera_record, track)
            return None

        # Verificar si podemos iniciar una nueva detección
        with detecting_lock:
            can_start = not detecting

        # Se ejecuta la inferencia YOLO asíncronamente cada settings.detect_every fotogramas
        if can_start and (frame_idx % settings.detect_every == 0 or not last_detections):
            with detecting_lock:
                detecting = True

            def _run_detection_async(frame_copy):
                nonlocal last_detections, detecting, captured, last_event_class, active_tracks
                try:
                    now = time.time()
                    # Verificar nuevamente si el hilo padre sigue vivo
                    parent_alive = any(t.name == f"camera-{camera_record['id']}" and t.is_alive() for t in threading.enumerate())
                    if not parent_alive:
                        for tid in list(active_tracks.keys()):
                            track = active_tracks[tid]
                            if not track["uploaded"]:
                                track["uploaded"] = True
                                with lock:
                                    captured.add(tid)
                                uploader.submit(save_detection, pb, camera_record, track)
                        return

                    detections = detector.process_frame(frame_copy)
                    with lock:
                        last_detections = detections

                    detected_tids = set()
                    for d in detections:
                        tid = d["tracker_id"]
                        detected_tids.add(tid)
                        
                        with lock:
                            if tid in captured:
                                continue
                            if d["confidence"] < settings.confidence_threshold:
                                continue
                            cls = d["class_id"]
                            if settings.event_cooldown > 0 and now - last_event_class.get(cls, 0) < settings.event_cooldown:
                                continue

                            x1, y1, x2, y2 = d["xyxy"]
                            area = (x2 - x1) * (y2 - y1)

                            # Inicializar tracker si es nuevo
                            if tid not in active_tracks:
                                active_tracks[tid] = {
                                    "class_name": d["class_name"],
                                    "confidence": d["confidence"],
                                    "class_id": d["class_id"],
                                    "best_area": area,
                                    "best_yolo_detection": d,
                                    "best_frame": frame_copy,
                                    "frames_seen": 1,
                                    "last_seen": now,
                                    "uploaded": False,
                                }
                            else:
                                track = active_tracks[tid]
                                if not track["uploaded"]:
                                    track["frames_seen"] += 1
                                    track["last_seen"] = now
                                    # Conservar la toma donde el área del vehículo sea mayor (más cerca de la cámara)
                                    if area > track["best_area"]:
                                        track["best_area"] = area
                                        track["best_yolo_detection"] = d
                                        track["best_frame"] = frame_copy

                        # Si ya lo rastreamos por al menos 15 fotogramas de detección,
                        # disparamos la lectura OCR y subida en segundo plano para ese mejor cuadro
                        track = active_tracks.get(tid)
                        if track and not track["uploaded"] and track["frames_seen"] >= 15:
                            track["uploaded"] = True
                            with lock:
                                captured.add(tid)
                                last_event_class[d["class_id"]] = now
                            uploader.submit(save_detection, pb, camera_record, track)

                    # Gestionar subida de vehículos que salieron de la pantalla antes del límite de fotogramas
                    for tid in list(active_tracks.keys()):
                        track = active_tracks[tid]
                        if not track["uploaded"] and (now - track["last_seen"] > 1.5):
                            # Se considera que el auto ya salió de la pantalla: subir el mejor cuadro recolectado
                            track["uploaded"] = True
                            with lock:
                                captured.add(tid)
                                last_event_class[track["class_id"]] = now
                            uploader.submit(save_detection, pb, camera_record, track)

                    # Limpiar memoria de tracks antiguos
                    for tid in list(active_tracks.keys()):
                        track = active_tracks[tid]
                        if track["uploaded"] or (now - track["last_seen"] > 30.0):
                            active_tracks.pop(tid, None)

                except Exception:
                    logger.exception("Error en detector asíncrono")
                finally:
                    with detecting_lock:
                        detecting = False

            threading.Thread(
                target=_run_detection_async,
                args=(frame.copy(),),
                daemon=True,
                name=f"yolo-worker-{camera_record['id']}"
            ).start()

        # En cada fotograma (que fluye a alta velocidad), dibujamos las últimas detecciones activas
        annotated = frame.copy()
        with lock:
            current_detections = list(last_detections)

        for d in current_detections:
            _draw_detection(annotated, d)

        if len(captured) > MAX_CAPTURED_IDS:
            with lock:
                captured.clear()

        ok, buffer = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        return buffer.tobytes() if ok else None

    return process_frame

"""Lectura de placas con EasyOCR (solo vehículos). Carga diferida y opcional.

Filtra resultados por formato, posición y proporción para descartar textos que
no son placas (carteles, publicidad, etc.). Opcionalmente usa un modelo YOLO
entrenado para detectar primero la región de la placa (PLATE_MODEL en .env).
"""
from __future__ import annotations

import logging
import re
import threading

import cv2
import numpy as np

logger = logging.getLogger("ocr_plate")


class PlateReader:
    def __init__(self, enabled: bool = True, plate_model_path: str = "") -> None:
        self.enabled = enabled
        self.plate_model_path = plate_model_path
        self._reader = None
        self._plate_model = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ lazy
    def _get_reader(self):
        if not self.enabled:
            return None
        if self._reader is None:
            with self._lock:
                if self._reader is None:
                    try:
                        from fast_plate_ocr import LicensePlateRecognizer

                        logger.info("Cargando fast-plate-ocr (CPU)...")
                        self._reader = LicensePlateRecognizer("cct-s-v2-global-model")
                    except Exception:
                        logger.exception("No se pudo inicializar fast-plate-ocr; OCR desactivado")
                        self.enabled = False
                        self._reader = None
        return self._reader

    def _get_plate_model(self):
        if not self.plate_model_path:
            return None
        if self._plate_model is None:
            with self._lock:
                if self._plate_model is None:
                    try:
                        from ultralytics import YOLO

                        logger.info("Cargando modelo detector de placas: %s", self.plate_model_path)
                        self._plate_model = YOLO(self.plate_model_path)
                    except Exception:
                        logger.exception("No se pudo cargar PLATE_MODEL; uso solo OCR heurístico")
                        self.plate_model_path = ""
                        self._plate_model = None
        return self._plate_model

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _correct_plate_spelling(text: str) -> str:
        """Corrige confusiones típicas de OCR (I por 1, O por 0, etc.) basadas en patrones sintácticos."""
        text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if len(text) < 4 or len(text) > 10:
            return text
            
        LETTERS_TO_DIGITS = {"I": "1", "L": "1", "O": "0", "Q": "0", "S": "5", "Z": "2", "B": "8", "G": "6"}
        DIGITS_TO_LETTERS = {"1": "I", "0": "O", "2": "Z", "5": "S", "8": "B", "6": "G"}
        
        # 1. Formato Reino Unido (7 caracteres): LL DD LLL (ej. NA13NRU)
        if len(text) == 7:
            letters_indices = [0, 1, 4, 5, 6]
            digits_indices = [2, 3]
            chars = list(text)
            for idx in letters_indices:
                if chars[idx] in DIGITS_TO_LETTERS:
                    chars[idx] = DIGITS_TO_LETTERS[chars[idx]]
            for idx in digits_indices:
                if chars[idx] in LETTERS_TO_DIGITS:
                    chars[idx] = LETTERS_TO_DIGITS[chars[idx]]
            return "".join(chars)
            
        # 2. Formato Europeo estándar (7 caracteres): DDDD LLL (ej. 1234BBB)
        if len(text) == 7 and sum(c.isdigit() for c in text[:4]) >= 2:
            chars = list(text)
            for i in range(4):
                if chars[i] in LETTERS_TO_DIGITS:
                    chars[i] = LETTERS_TO_DIGITS[chars[i]]
            for i in range(4, 7):
                if chars[i] in DIGITS_TO_LETTERS:
                    chars[i] = DIGITS_TO_LETTERS[chars[i]]
            return "".join(chars)
            
        # 3. Formato Mercosur / Chile / Colombia (6 caracteres): LLLL DD o LLL DD (ej. BBCC12)
        if len(text) == 6:
            letters_indices = [0, 1, 2, 3]
            digits_indices = [4, 5]
            chars = list(text)
            for idx in letters_indices:
                if chars[idx] in DIGITS_TO_LETTERS:
                    chars[idx] = DIGITS_TO_LETTERS[chars[idx]]
            for idx in digits_indices:
                if chars[idx] in LETTERS_TO_DIGITS:
                    chars[idx] = LETTERS_TO_DIGITS[chars[idx]]
            return "".join(chars)
            
        return text

    @staticmethod
    def _looks_like_plate(clean: str, require_digit: bool) -> bool:
        if not re.fullmatch(r"[A-Z0-9]{4,8}", clean):
            return False
        if not any(c.isalpha() for c in clean):
            return False
        if require_digit and not any(c.isdigit() for c in clean):
            return False
        return True

    @staticmethod
    def _box_metrics(bbox, h: int, w: int):
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        box_w = max(xs) - min(xs)
        box_h = max(ys) - min(ys)
        ratio = box_w / max(1.0, box_h)
        ycenter = (min(ys) + max(ys)) / 2.0 / max(1, h)
        xcenter = (min(xs) + max(xs)) / 2.0 / max(1, w)
        return ratio, ycenter, xcenter

    @staticmethod
    def _preprocess_region(region):
        """Upscale para mejorar la lectura de placas pequeñas."""
        h, w = region.shape[:2]
        scale = max(2.0, 320.0 / max(1, w))
        return cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # ------------------------------------------------------------- public
    def read_plate(self, crop) -> str:
        return self.read_plate_details(crop)[0]

    def read_plate_details(self, crop) -> tuple[str, tuple[int, int, int, int] | None, float]:
        """Devuelve la placa leída o 'SIN_PLACA_DETECTADA'."""
        reader = self._get_reader()
        if reader is None or crop is None or crop.size == 0:
            return "SIN_PLACA_DETECTADA", None, 0.0
        try:
            region, located, offset = self._crop_plate_region(crop)
            if not located or region.size == 0:
                # Si no se localizó la placa con el modelo YOLO, usamos el crop completo
                region = crop
                offset = (0, 0, crop.shape[1], crop.shape[0])

            # Inferencia con fast-plate-ocr (espera un ndarray y devuelve una lista de resultados)
            preds = reader.run(region, return_confidence=True)
            if not preds:
                return "SIN_PLACA_DETECTADA", None, 0.0

            pred = preds[0]
            text = pred.plate
            
            # Obtener probabilidad de los caracteres
            if hasattr(pred, "char_probs") and pred.char_probs is not None and len(pred.char_probs) > 0:
                ocr_prob = float(np.mean(pred.char_probs))
            else:
                ocr_prob = float(getattr(pred, "prob", getattr(pred, "confidence", 1.0)))

            # Limpiar y corregir el texto detectado
            clean = re.sub(r"[^A-Z0-9]", "", text.upper())
            clean = self._correct_plate_spelling(clean)

            return clean, offset, ocr_prob
        except Exception:
            logger.exception("Error leyendo placa")
            return "SIN_PLACA_DETECTADA", None, 0.0

    def _crop_plate_region(self, crop):
        """Si hay un modelo detector de placas, recorta solo la región de la placa."""
        model = self._get_plate_model()
        if model is None:
            return crop, False, (0, 0, 0, 0)
        # Bajamos el umbral de confianza a 0.15 para captar placas más lejanas o pequeñas
        results = model(crop, conf=0.15, verbose=False)[0]
        boxes = results.boxes
        if boxes is None or len(boxes) == 0:
            return crop, False, (0, 0, 0, 0)
        h, w = crop.shape[:2]
        best_box = None
        best_conf = 0.0
        for box, conf in zip(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy()):
            if float(conf) > best_conf:
                best_conf = float(conf)
                best_box = [int(v) for v in box]
        if best_box is None:
            return crop, False, (0, 0, 0, 0)
        
        x1, y1, x2, y2 = best_box
        box_w = x2 - x1
        box_h = y2 - y1
        
        # Añadir un 15% de margen (padding) alrededor de la placa
        # Esto da "aire" a los caracteres de los bordes y mejora drásticamente el OCR
        pad_w = int(box_w * 0.15)
        pad_h = int(box_h * 0.15)
        
        x1 = max(0, x1 - pad_w)
        y1 = max(0, y1 - pad_h)
        x2 = min(w, x2 + pad_w)
        y2 = min(h, y2 + pad_h)
        
        region = crop[y1:y2, x1:x2]
        return (region if region.size > 0 else crop), True, (x1, y1, x2, y2)


_reader_singleton: PlateReader | None = None


def get_plate_reader() -> PlateReader:
    global _reader_singleton
    if _reader_singleton is None:
        from backend.core.config import settings

        _reader_singleton = PlateReader(enabled=settings.enable_ocr, plate_model_path=settings.plate_model)
    return _reader_singleton

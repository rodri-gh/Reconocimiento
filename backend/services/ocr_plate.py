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
                        import easyocr

                        logger.info("Cargando EasyOCR (CPU)...")
                        self._reader = easyocr.Reader(["es", "en"], gpu=False, verbose=False)
                    except Exception:
                        logger.exception("No se pudo inicializar EasyOCR; OCR desactivado")
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

            # --- CORRECCIÓN DE PERSPECTIVA (DESKEWING) ---
            if located and region.size > 0:
                try:
                    # Convertir a escala de grises
                    plate_gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
                    # Umbralizado para aislar caracteres
                    _, thresh = cv2.threshold(plate_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                    # Encontrar coordenadas de píxeles activos (caracteres)
                    coords = np.column_stack(np.where(thresh > 0))
                    if coords.size > 0:
                        # Encontrar el rectángulo rotado mínimo de las características
                        rect = cv2.minAreaRect(coords)
                        angle = rect[-1]
                        
                        # Normalizar el ángulo de rotación
                        if angle < -45:
                            angle = -(90 + angle)
                        else:
                            angle = -angle
                        
                        # Rotar el recorte si tiene inclinación moderada y corregible
                        if 1.0 < abs(angle) < 25.0:
                            (h_reg, w_reg) = region.shape[:2]
                            center = (w_reg // 2, h_reg // 2)
                            M = cv2.getRotationMatrix2D(center, angle, 1.0)
                            region = cv2.warpAffine(region, M, (w_reg, h_reg), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
                except Exception:
                    logger.exception("Fallo al corregir perspectiva de la placa")

            processed = self._preprocess_region(region)
            scale = processed.shape[1] / max(1, region.shape[1])
            gray = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
            h, w = gray.shape[:2]
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            
            # Usamos el color original (processed) y el gris mejorado (enhanced).
            # Evitamos la binarización (threshold) ya que destruye detalles y confunde al OCR en CPU.
            variants = (processed, enhanced)
            best: tuple[str, float, tuple[int, int, int, int] | None] = ("SIN_PLACA_DETECTADA", 0.0, None)
            
            for variant in variants:
                results = reader.readtext(
                    variant,
                    allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                    detail=1,
                    paragraph=False,
                    width_ths=0.7,  # Unir bloques de texto cercanos (como espacios en la placa)
                )
                if not results:
                    continue

                if located:
                    # El modelo ya localizó la placa: confiamos en la caja del modelo YOLO (offset).
                    # Ordenamos las partes detectadas de izquierda a derecha.
                    results_sorted = sorted(results, key=lambda r: min(p[0] for p in r[0]))
                    valid_parts = []
                    total_prob = 0.0
                    for bbox, text, prob in results_sorted:
                        clean = re.sub(r"[^A-Z0-9]", "", text.upper())
                        # Aplicar corrección de ortografía LPR
                        clean = self._correct_plate_spelling(clean)
                        # Filtramos textos basura vacíos o con muy baja probabilidad
                        if len(clean) >= 1 and prob >= 0.1:
                            valid_parts.append(clean)
                            total_prob += prob
                    
                    if valid_parts:
                        combined_text = "".join(valid_parts)
                        # Volver a corregir el texto completo concatenado
                        combined_text = self._correct_plate_spelling(combined_text)
                        avg_prob = total_prob / len(valid_parts)
                        # Usamos la caja completa detectada por el modelo YOLO (offset)
                        local_box = offset
                        # Priorizar textos más largos si tienen una confianza mínima razonable
                        if avg_prob > best[1] or (len(combined_text) > len(best[0]) and avg_prob > 0.15):
                            best = (combined_text, avg_prob, local_box)
                else:
                    # Sin modelo: filtros estrictos para evitar falsos positivos.
                    for bbox, text, prob in results:
                        clean = re.sub(r"[^A-Z0-9]", "", text.upper())
                        clean = self._correct_plate_spelling(clean)
                        if prob < 0.3 or not self._looks_like_plate(clean, require_digit=True):
                            continue
                        ratio, ycenter, _ = self._box_metrics(bbox, h, w)
                        if not (2.0 <= ratio <= 10.0):
                            continue
                        if not (0.35 <= ycenter <= 0.95):
                            continue
                        xs = [p[0] for p in bbox]
                        ys = [p[1] for p in bbox]
                        local_box = (
                            int(min(xs) / scale + offset[0]),
                            int(min(ys) / scale + offset[1]),
                            int(max(xs) / scale + offset[0]),
                            int(max(ys) / scale + offset[1]),
                        )
                        if prob > best[1]:
                            best = (clean, prob, local_box)
            return best[0], best[2], best[1]
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

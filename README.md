# Reconocimiento — Detector de movimiento + panel web

Sistema para vigilar cámaras IP (locales o públicas) y capturar **solo los eventos**:
cuando pasa una persona o un vehículo se guarda una foto y un registro. Sin guardar
video continuo.

- **Backend:** Python + FastAPI (gestión de cámaras + detector YOLO/ByteTrack).
- **Base de datos:** PocketBase (imágenes incluidas como archivos; S3/R2 configurable desde su admin).
- **Frontend:** React + Vite (Fase 3).
- **Detector:** YOLO + ByteTrack + EasyOCR (placas).

## Estructura

```
backend/
├─ main.py                  # API FastAPI
├─ seed_examples.py         # precarga cámaras de ejemplo
├─ core/
│  ├─ config.py             # .env via pydantic-settings
│  └─ crypto.py             # encriptación de contraseñas RTSP (Fernet)
├─ services/
│  ├─ pocketbase_client.py  # cliente HTTP de PocketBase (+ subida de archivos)
│  ├─ camera_manager.py     # workers por cámara + test de conexión
│  ├─ detector.py           # YOLO + ByteTrack
│  ├─ ocr_plate.py          # EasyOCR para placas (opcional)
│  └─ event_pipeline.py     # debounce por tracker_id + captura a PocketBase
└─ tools/
   └─ mjpeg_server.py       # cámara MJPEG local de prueba (--image bus.jpg)
infra/                      # pocketbase.exe local + docker-compose (Fase 4)
frontend/                   # panel web (Fase 3)
```

## Requisitos

- Python 3.11+
- PocketBase (local o remoto). El `.env` apunta a `https://camara.softcore.dev`.

## Puesta en marcha (desarrollo)

```bash
pip install -r backend/requirements.txt
cp .env.example .env    # completa los valores (o usa el .env ya creado)
python -m backend.tools.mjpeg_server   # cámara MJPEG local opcional (puerto 8081)
python -m backend.seed_examples        # precarga cámaras de ejemplo
python -m uvicorn backend.main:app --reload --port 8000
```

Servidor en `http://127.0.0.1:8000` — docs automáticas en `/docs`.

## Panel web

```bash
cd frontend
npm install
npm run dev
```

Panel en `http://127.0.0.1:5173`. El frontend usa `VITE_API_URL` para localizar
FastAPI; por defecto apunta a `http://127.0.0.1:8000`.

Incluye Dashboard, gestión de cámaras, prueba de conexión, inicio/detención del
detector, galería de eventos, filtros y detalle con la imagen anotada.

## Cómo conectarse a una cámara

Se registra una URL RTSP, HTTP-MJPEG o snapshot. Usuario/contraseña son **opcionales**
(las cámaras públicas normalmente no los piden):

```text
rtsp://ip-publica:554/stream           # cámara IP (RTSP)
http://ip:8080/video.mjpg              # cámara web (MJPEG)
http://ip/cam.jpg                      # snapshot repetido
rtsp://192.168.1.100:554/stream1       # IP local (solo accesible si el backend corre en esa red)
```

### Endpoints principales

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/cameras` | registrar cámara |
| GET | `/api/cameras` | listar |
| PATCH | `/api/cameras/{id}` | actualizar |
| DELETE | `/api/cameras/{id}` | eliminar |
| POST | `/api/cameras/{id}/test` | probar conexión (resolución, FPS, códec) |
| POST | `/api/cameras/{id}/start` | iniciar monitoreo |
| POST | `/api/cameras/{id}/stop` | detener |
| GET | `/api/cameras/{id}/status` | estado del worker |
| GET | `/api/detections` | eventos detectados |

## Nota sobre cámaras locales vs. públicas

El backend se conecta a la URL que registres. Si la URL es una IP privada
(`192.168.x.x`), solo funcionará cuando el backend corra en esa misma red. Para la
demo en un VPS se usan cámaras con URL pública.

## Lectura de placas

1. YOLO detecta el vehículo (auto, moto, bus, camión).
2. Un **modelo detector de placas** (`backend/models/license_plate.pt`, clase `license_plate`) localiza la placa dentro del recorte.
3. EasyOCR lee solo esa región.
4. Si no se detecta placa o no se lee bien → se guarda `SIN_PLACA_DETECTADA` (el evento del auto se registra igual).

El modelo de placas se configura con `PLATE_MODEL` en `.env` (vacío = OCR heurístico sobre todo el recorte, menos confiable).

## Fases pendientes

1. ~~Backend base (API + cámaras + test conexión)~~ ✅
2. ~~Detector YOLO + ByteTrack + debounce (evento → foto en PocketBase)~~ ✅
2.1 ~~Modelo detector de placas + OCR~~ ✅
3. Frontend React (Dashboard, Cámaras, Eventos)
4. Docker Compose + despliegue en VPS + HTTPS
5. Extras: vista en vivo HLS, filtros, OCR de placas con modelo dedicado

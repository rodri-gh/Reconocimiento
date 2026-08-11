"""API FastAPI del proyecto: gestión de cámaras + eventos (Fase 1)."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.core.crypto import encrypt_secret
from backend.services import camera_manager as cm
from backend.services.camera_manager import manager as camera_manager
from backend.services.event_pipeline import build_process_frame
from backend.services.pocketbase_client import PocketBaseClient, PocketBaseError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")

pb = PocketBaseClient(
    settings.pocketbase_url,
    settings.pocketbase_admin_email,
    settings.pocketbase_admin_password,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        pb.authenticate()
        pb.ensure_collections()
        logger.info("PocketBase listo (%s)", settings.pocketbase_url)
    except Exception:
        logger.exception("No se pudo inicializar PocketBase. Revisa credenciales en .env")
        raise

    def sync_state(camera_id: str, state: dict) -> None:
        from datetime import datetime

        last_seen = ""
        if state.get("last_seen"):
            last_seen = datetime.fromtimestamp(state["last_seen"]).isoformat()
        try:
            pb.update_camera(camera_id, {"status": state["status"], "last_seen": last_seen, "last_error": state.get("last_error", "") or ""})
        except Exception:
            logger.debug("Sync estado cámara %s falló", camera_id)

    camera_manager.start_sync_loop(sync_state)
    yield
    camera_manager.stop_all()


app = FastAPI(title="Detector de Movimiento API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ schemas
class CameraCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    rtsp_url: str = Field(..., min_length=1, max_length=500)
    username: str | None = None
    password: str | None = None
    stream_type: str | None = None
    enabled: bool = True


class CameraUpdate(BaseModel):
    name: str | None = None
    rtsp_url: str | None = None
    username: str | None = None
    password: str | None = None
    stream_type: str | None = None
    enabled: bool | None = None


class DetectionQuery(BaseModel):
    camera: str | None = None
    object_type: str | None = None
    plate_text: str | None = None
    limit: int = 50
    page: int = 1


# --------------------------------------------------------------- helpers
def _to_response(record: dict) -> dict:
    return {
        "id": record["id"],
        "name": record.get("name", ""),
        "rtsp_url": record.get("rtsp_url", ""),
        "username": record.get("username", ""),
        "stream_type": record.get("stream_type", ""),
        "status": record.get("status", "idle"),
        "enabled": bool(record.get("enabled", False)),
        "last_seen": record.get("last_seen"),
        "last_error": record.get("last_error"),
        "created": record.get("created"),
        "updated": record.get("updated"),
    }


def _validate_stream_type(value: str, url: str) -> str:
    if value:
        if value not in cm.SUPPORTED_STREAM_TYPES:
            raise HTTPException(400, f"stream_type debe ser uno de: {cm.SUPPORTED_STREAM_TYPES}")
        return value
    try:
        return cm.detect_stream_type(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _build_payload(data: dict, is_create: bool) -> dict:
    url = data.get("rtsp_url")
    if is_create and not url:
        raise HTTPException(422, "rtsp_url es obligatorio")
    payload: dict = {}
    if url:
        payload["rtsp_url"] = url
    if "name" in data and data["name"] is not None:
        payload["name"] = data["name"]
    if "username" in data:
        payload["username"] = data.get("username") or ""
    if "enabled" in data and data["enabled"] is not None:
        payload["enabled"] = data["enabled"]
    if "password" in data and data["password"] is not None:
        if data["password"]:
            payload["password_encrypted"] = encrypt_secret(data["password"])
        else:
            payload["password_encrypted"] = ""
    if "stream_type" in data or url:
        stype = _validate_stream_type(data.get("stream_type") or "", url or "")
        payload["stream_type"] = stype
    return payload


def _get_camera_or_404(camera_id: str) -> dict:
    record = pb.get_camera(camera_id)
    if not record:
        raise HTTPException(404, "Cámara no encontrada")
    return record


# ------------------------------------------------------------------- health
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "pocketbase": settings.pocketbase_url}


# -------------------------------------------------------------- cameras CRUD
@app.post("/api/cameras", status_code=201)
def create_camera(body: CameraCreate) -> dict:
    payload = _build_payload(body.model_dump(), is_create=True)
    record = pb.create_camera(payload)
    return _to_response(record)


@app.get("/api/cameras")
def list_cameras() -> list[dict]:
    records = pb.list_cameras()
    return [_to_response(r) for r in records]


@app.get("/api/cameras/{camera_id}")
def get_camera(camera_id: str) -> dict:
    record = _get_camera_or_404(camera_id)
    return _to_response(record)


@app.patch("/api/cameras/{camera_id}")
def update_camera(camera_id: str, body: CameraUpdate) -> dict:
    _get_camera_or_404(camera_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "No hay campos para actualizar")
    payload = _build_payload(data, is_create=False)
    record = pb.update_camera(camera_id, payload)
    return _to_response(record)


@app.delete("/api/cameras/{camera_id}", status_code=204)
def delete_camera(camera_id: str) -> None:
    _get_camera_or_404(camera_id)
    camera_manager.stop(camera_id)
    pb.delete_camera(camera_id)


# ------------------------------------------------------- test / start / stop
@app.post("/api/cameras/{camera_id}/test")
def test_camera(camera_id: str) -> dict:
    record = _get_camera_or_404(camera_id)
    try:
        result = cm.test_connection(record)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error probando cámara %s", camera_id)
        raise HTTPException(500, f"Error probando cámara: {exc}") from exc
    return result


@app.post("/api/cameras/{camera_id}/start")
def start_camera(camera_id: str) -> dict:
    record = _get_camera_or_404(camera_id)
    process_frame = build_process_frame(pb, record)
    camera_manager.start(record, process_frame=process_frame, process_every=settings.detect_every)
    return {"id": camera_id, "status": "starting"}


@app.post("/api/cameras/{camera_id}/stop")
def stop_camera(camera_id: str) -> dict:
    _get_camera_or_404(camera_id)
    camera_manager.stop(camera_id)
    pb.update_camera(camera_id, {"status": "idle"})
    return {"id": camera_id, "status": "stopped"}


@app.get("/api/cameras/{camera_id}/status")
def camera_status(camera_id: str) -> dict:
    _get_camera_or_404(camera_id)
    state = camera_manager.status(camera_id)
    return state or {"camera_id": camera_id, "status": "idle"}


@app.get("/api/status")
def all_statuses() -> dict:
    return camera_manager.statuses()


@app.get("/api/cameras/{camera_id}/live.mjpg")
def live_camera(camera_id: str):
    _get_camera_or_404(camera_id)
    if not camera_manager.is_running(camera_id):
        raise HTTPException(409, "Inicia el detector antes de abrir la vista en vivo")

    def frames():
        while camera_manager.is_running(camera_id):
            jpeg = camera_manager.latest_jpeg(camera_id)
            if jpeg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
            time.sleep(0.08)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


# --------------------------------------------------------------- detections
@app.get("/api/detections")
def list_detections(camera: str | None = None, object_type: str | None = None, limit: int = 100) -> list[dict]:
    params = {"perPage": min(limit, 1000), "sort": "-detected_at"}
    filters: list[str] = []
    if camera:
        filters.append(f"camera.id='{camera}'")
    if object_type:
        filters.append(f"object_type='{object_type}'")
    if filters:
        params["filter"] = " && ".join(filters)
    try:
        records = pb.list_detections(params)
    except PocketBaseError as exc:
        raise HTTPException(502, f"Error leyendo detecciones: {exc}") from exc
    for r in records:
        r["image_url"] = PocketBaseClient.file_url(settings.pocketbase_url, r, "image")
        r["image_thumb_url"] = PocketBaseClient.file_url(settings.pocketbase_url, r, "image", thumb="250x250")
    return records


@app.delete("/api/detections/{detection_id}", status_code=204)
def delete_detection(detection_id: str) -> None:
    try:
        pb.delete_detection(detection_id)
    except PocketBaseError as exc:
        raise HTTPException(502, f"Error eliminando detección: {exc}") from exc


@app.delete("/api/detections", status_code=204)
def delete_all_detections() -> None:
    try:
        pb.delete_all_detections()
    except PocketBaseError as exc:
        raise HTTPException(502, f"Error vaciando historial de detecciones: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.api_host, port=settings.api_port, reload=True)

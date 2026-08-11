"""Precarga cámaras de ejemplo en PocketBase (demo).

Uso:
    python -m backend.seed_examples

Las URLs públicas pueden caerse; la cámara local MJPEG es la más estable si
corres el servidor de prueba (ver docs/servidor_mjpeg_local.md).
"""
from __future__ import annotations

import sys

from backend.core.config import settings
from backend.services.camera_manager import detect_stream_type
from backend.services.pocketbase_client import PocketBaseClient

EXAMPLE_CAMERAS = [
    {
        "name": "Demo RTSP - Wowza (Big Buck Bunny)",
        "rtsp_url": "rtsp://wowzaec2demo.streamlock.net/vod/mp4:BigBuckBunny_115k.mov",
        "username": "",
        "password": "",
    },
    {
        "name": "Demo MJPEG - Local",
        "rtsp_url": "http://127.0.0.1:8081/video.mjpg",
        "username": "",
        "password": "",
    },
    {
        "name": "Demo RTSP - Otro stream publico",
        "rtsp_url": "rtsp://185.20.222.221:8554/vod/tearsofsteel_720p.mov",
        "username": "",
        "password": "",
    },
]


def main() -> None:
    pb = PocketBaseClient(
        settings.pocketbase_url,
        settings.pocketbase_admin_email,
        settings.pocketbase_admin_password,
    )
    pb.authenticate()
    pb.ensure_collections()

    existing_names = {c["name"] for c in pb.list_cameras()}
    for cam in EXAMPLE_CAMERAS:
        if cam["name"] in existing_names:
            print(f"  - ya existe: {cam['name']}")
            continue
        payload = dict(cam)
        payload["stream_type"] = detect_stream_type(cam["rtsp_url"])
        pb.create_camera(payload)
        print(f"  + creada: {cam['name']} -> {cam['rtsp_url']}")

    print(f"\nListo. Total cámaras: {len(pb.list_cameras())}")


if __name__ == "__main__":
    sys.exit(main())

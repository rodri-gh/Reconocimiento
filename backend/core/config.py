"""Configuración central: carga variables del .env via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PocketBase
    pocketbase_url: str = "http://127.0.0.1:8090"
    pocketbase_admin_email: str = ""
    pocketbase_admin_password: str = ""

    # Seguridad
    encryption_key: str = ""

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Detector (Fase 2)
    yolo_model: str = "yolov8n.pt"
    confidence_threshold: float = 0.5
    detect_every: int = 3
    event_cooldown: float = 5.0
    enable_ocr: bool = False
    plate_model: str = ""

    @property
    def fernet(self) -> Fernet:
        if not self.encryption_key:
            raise RuntimeError("Falta ENCRYPTION_KEY en el .env")
        return Fernet(self.encryption_key.encode())


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

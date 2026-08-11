"""Encriptación/desencriptación de contraseñas RTSP usando Fernet."""
from __future__ import annotations

from backend.core.config import settings


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return settings.fernet.encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    return settings.fernet.decrypt(token.encode()).decode()

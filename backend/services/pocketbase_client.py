"""Cliente HTTP mínimo para la API de PocketBase (auth admin + colecciones + records)."""
from __future__ import annotations

import time

import requests


class PocketBaseError(Exception):
    pass


class PocketBaseClient:
    def __init__(self, base_url: str, admin_email: str, admin_password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_email = admin_email
        self.admin_password = admin_password
        self._token: str = ""
        self._token_expires: float = 0

    # ------------------------------------------------------------------ auth
    def authenticate(self) -> str:
        resp = requests.post(
            f"{self.base_url}/api/collections/_superusers/auth-with-password",
            json={"identity": self.admin_email, "password": self.admin_password},
            timeout=10,
        )
        if resp.status_code != 200:
            raise PocketBaseError(f"Auth PocketBase falló ({resp.status_code}): {resp.text}")
        data = resp.json()
        self._token = data["token"]
        # El token expira a los 3 días; re-autenticamos antes.
        self._token_expires = time.time() + 3 * 24 * 3600 - 60
        return self._token

    def _ensure_token(self) -> None:
        if not self._token or time.time() > self._token_expires:
            self.authenticate()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        self._ensure_token()
        headers = {
            "Authorization": self._token,
            **(kwargs.pop("headers", {}) or {}),
        }
        resp = requests.request(method, f"{self.base_url}{path}", headers=headers, timeout=kwargs.pop("timeout", 15), **kwargs)
        if resp.status_code in (401, 403):
            # Token vencido/inválido: reintentamos una vez.
            self.authenticate()
            headers["Authorization"] = self._token
            resp = requests.request(method, f"{self.base_url}{path}", headers=headers, timeout=15, **kwargs)
        return resp

    # ------------------------------------------------------------ colecciones
    def list_collections(self) -> list[dict]:
        resp = self._request("GET", "/api/collections", params={"perPage": 200})
        if resp.status_code != 200:
            raise PocketBaseError(f"Listar colecciones falló ({resp.status_code}): {resp.text}")
        return resp.json()["items"]

    def get_collection(self, name: str) -> dict | None:
        resp = self._request("GET", f"/api/collections/{name}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise PocketBaseError(f"Obtener colección {name} falló ({resp.status_code}): {resp.text}")
        return resp.json()

    def create_collection(self, name: str, fields: list[dict]) -> dict:
        payload = {
            "name": name,
            "type": "base",
            "fields": fields,
            "listRule": "",
            "viewRule": "",
            "createRule": None,
            "updateRule": None,
            "deleteRule": None,
        }
        resp = self._request("POST", "/api/collections", json=payload)
        if resp.status_code not in (200, 201):
            raise PocketBaseError(f"Crear colección {name} falló ({resp.status_code}): {resp.text}")
        return resp.json()

    def ensure_collections(self) -> None:
        existing = {c["name"] for c in self.list_collections()}
        cameras_id = ""
        if "cameras" not in existing:
            coll = self.create_collection(
                "cameras",
                [
                    {"name": "name", "type": "text", "required": True, "max": 120},
                    {"name": "rtsp_url", "type": "text", "required": True, "max": 500},
                    {"name": "username", "type": "text", "max": 120},
                    {"name": "password_encrypted", "type": "text", "max": 500},
                    {"name": "stream_type", "type": "select", "maxSelect": 1, "values": ["rtsp", "mjpeg", "snapshot"], "required": True},
                    {"name": "status", "type": "select", "maxSelect": 1, "values": ["idle", "connecting", "running", "error", "offline"]},
                    {"name": "enabled", "type": "bool"},
                    {"name": "last_seen", "type": "date"},
                    {"name": "last_error", "type": "text", "max": 500},
                    {"name": "created_at", "type": "date", "system": False},
                ],
            )
            cameras_id = coll["id"]
        else:
            cameras_id = self.get_collection("cameras")["id"]
        if "detections" not in existing:
            self.create_collection(
                "detections",
                [
                    {"name": "camera", "type": "relation", "collectionId": cameras_id, "maxSelect": 1, "cascadeDelete": True},
                    {"name": "object_type", "type": "select", "maxSelect": 1, "values": ["Persona", "Auto", "Moto", "Bus", "Camion", "Otro"]},
                    {"name": "plate_text", "type": "text", "max": 30},
                    {"name": "confidence", "type": "number"},
                    {"name": "tracker_id", "type": "number"},
                    {"name": "image", "type": "file", "maxSelect": 1, "maxSize": 5242880, "mimeTypes": ["image/jpeg", "image/png"]},
                    {"name": "detected_at", "type": "date"},
                ],
            )

    # ------------------------------------------------------------ cameras CRUD
    def list_cameras(self) -> list[dict]:
        resp = self._request("GET", "/api/collections/cameras/records", params={"perPage": 200, "sort": "created_at"})
        if resp.status_code != 200:
            raise PocketBaseError(f"Listar cámaras falló ({resp.status_code}): {resp.text}")
        return resp.json()["items"]

    def get_camera(self, camera_id: str) -> dict | None:
        resp = self._request("GET", f"/api/collections/cameras/records/{camera_id}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise PocketBaseError(f"Obtener cámara {camera_id} falló ({resp.status_code}): {resp.text}")
        return resp.json()

    def create_camera(self, data: dict) -> dict:
        resp = self._request("POST", "/api/collections/cameras/records", json=data)
        if resp.status_code not in (200, 201):
            raise PocketBaseError(f"Crear cámara falló ({resp.status_code}): {resp.text}")
        return resp.json()

    def update_camera(self, camera_id: str, data: dict) -> dict:
        resp = self._request("PATCH", f"/api/collections/cameras/records/{camera_id}", json=data)
        if resp.status_code != 200:
            raise PocketBaseError(f"Actualizar cámara {camera_id} falló ({resp.status_code}): {resp.text}")
        return resp.json()

    def delete_camera(self, camera_id: str) -> None:
        resp = self._request("DELETE", f"/api/collections/cameras/records/{camera_id}")
        if resp.status_code not in (200, 204):
            raise PocketBaseError(f"Eliminar cámara {camera_id} falló ({resp.status_code}): {resp.text}")

    # ------------------------------------------------------------ detections
    def create_detection(self, data: dict) -> dict:
        resp = self._request("POST", "/api/collections/detections/records", json=data)
        if resp.status_code not in (200, 201):
            raise PocketBaseError(f"Crear detección falló ({resp.status_code}): {resp.text}")
        return resp.json()

    def create_record_with_file(
        self,
        collection: str,
        data: dict,
        file_field: str,
        file_bytes: bytes,
        filename: str,
        mime: str = "image/jpeg",
    ) -> dict:
        """Crea un record con un archivo adjunto (multipart/form-data)."""
        files = {file_field: (filename, file_bytes, mime)}
        resp = self._request("POST", f"/api/collections/{collection}/records", data=data, files=files)
        if resp.status_code not in (200, 201):
            raise PocketBaseError(f"Crear record con archivo falló ({resp.status_code}): {resp.text}")
        return resp.json()

    @staticmethod
    def file_url(base_url: str, record: dict, field: str = "image", thumb: str | None = None) -> str:
        """URL pública de un archivo de PocketBase (con thumb opcional)."""
        fname = record.get(field) or ""
        if not fname:
            return ""
        collection_id = record.get("collectionId") or ""
        record_id = record.get("id") or ""
        base = base_url.rstrip("/")
        thumb = f"?thumb={thumb}" if thumb else ""
        return f"{base}/api/files/{collection_id}/{record_id}/{fname}{thumb}"

    def list_detections(self, params: dict | None = None) -> list[dict]:
        default = {"perPage": 50, "sort": "-detected_at"}
        if params:
            default.update(params)
        resp = self._request("GET", "/api/collections/detections/records", params=default)
        if resp.status_code != 200:
            raise PocketBaseError(f"Listar detecciones falló ({resp.status_code}): {resp.text}")
        return resp.json()["items"]

    def delete_detection(self, detection_id: str) -> None:
        resp = self._request("DELETE", f"/api/collections/detections/records/{detection_id}")
        if resp.status_code not in (200, 204):
            raise PocketBaseError(f"Eliminar detección {detection_id} falló ({resp.status_code}): {resp.text}")

    def delete_detection_safe(self, detection_id: str) -> bool:
        """Elimina un registro de detección; retorna True si se eliminó, False si ya no existía (404)."""
        resp = self._request("DELETE", f"/api/collections/detections/records/{detection_id}")
        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 404:
            return False  # ya fue eliminado (cascade, concurrencia, etc.)
        raise PocketBaseError(f"Eliminar detección {detection_id} falló ({resp.status_code}): {resp.text}")

    def delete_all_detections(self) -> int:
        """Elimina TODOS los registros de detecciones de forma paralela para evitar timeouts."""
        from concurrent.futures import ThreadPoolExecutor
        
        # 1. Recolectar todos los IDs primero para evitar problemas de paginación dinámica
        all_ids = []
        page = 1
        while True:
            resp = self._request("GET", "/api/collections/detections/records", params={
                "perPage": 500,
                "page": page,
                "fields": "id"
            })
            if resp.status_code != 200:
                raise PocketBaseError(f"Listar detecciones para vaciar falló: {resp.text}")
            data = resp.json()
            items = data.get("items", [])
            if not items:
                break
            all_ids.extend([item["id"] for item in items])
            if len(items) < 500:
                break
            page += 1

        if not all_ids:
            return 0

        # 2. Borrar en paralelo usando un pool de hilos (hasta 15 trabajadores)
        total_deleted = 0
        with ThreadPoolExecutor(max_workers=15) as executor:
            # Lanzamos todas las tareas de borrado seguro
            results = executor.map(self.delete_detection_safe, all_ids)
            # Consumimos el generador para asegurar la ejecución
            for res in results:
                if res:
                    total_deleted += 1

        return total_deleted

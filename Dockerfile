# =============================================================================
# Sentinel – Contenedor único (Frontend + Backend)
# PocketBase es externo (configurado via POCKETBASE_URL en las env vars de Coolify)
# =============================================================================

# ─── Stage 1: Build del frontend ─────────────────────────────────────────────
FROM node:20-alpine AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# La API se sirve desde el mismo dominio vía Nginx reverse-proxy
ENV VITE_API_URL=""
RUN npm run build

# ─── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim

# Dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ─── Backend Python ──────────────────────────────────────────────────────────
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY yolov8n.pt /app/yolov8n.pt

# ─── Frontend estático ───────────────────────────────────────────────────────
COPY --from=frontend-build /build/dist /var/www/html

# ─── Configuración ───────────────────────────────────────────────────────────
COPY deploy/nginx.conf /etc/nginx/sites-available/default
COPY deploy/supervisord.conf /etc/supervisor/conf.d/sentinel.conf
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Coolify expone este puerto
EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]

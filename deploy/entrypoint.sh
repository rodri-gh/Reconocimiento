#!/bin/bash
set -e

echo "════════════════════════════════════════════"
echo "  SENTINEL – Starting services..."
echo "════════════════════════════════════════════"

# Generar ENCRYPTION_KEY si no fue provista
if [ -z "$ENCRYPTION_KEY" ]; then
    echo "[entrypoint] Generando ENCRYPTION_KEY automática..."
    export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
fi

# Activar el site de Nginx
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default

echo "[entrypoint] PocketBase externo: ${POCKETBASE_URL:-no configurado}"
echo "[entrypoint] Backend en :8000 | Nginx en :80"
echo "════════════════════════════════════════════"

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/sentinel.conf

#!/usr/bin/env bash
# One-shot VM provisioning. Run as root on a fresh Debian/Ubuntu VM.
# Usage: sudo bash setup.sh [your-domain-or-ip]
set -euo pipefail

DOMAIN=${1:-""}
APP_DIR="/opt/stock-research"
APP_USER="stock"
VENV="$APP_DIR/venv"

# System deps
apt-get update -q
apt-get install -y -q python3.12 python3.12-venv git nginx certbot python3-certbot-nginx \
    gcc libxml2-dev libxslt-dev

# App user
useradd -r -m -s /bin/bash "$APP_USER" 2>/dev/null || true

# Clone / update repo
if [ -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git -C "$APP_DIR" pull
else
    git clone https://github.com/skarin7/stock-research "$APP_DIR"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

# Virtualenv + deps
sudo -u "$APP_USER" python3.12 -m venv "$VENV"
sudo -u "$APP_USER" "$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# systemd services
cp "$APP_DIR/deploy/vm/stock-chat.service" /etc/systemd/system/
cp "$APP_DIR/deploy/vm/stock-scheduler.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable stock-chat
systemctl restart stock-chat
systemctl enable stock-scheduler
systemctl restart stock-scheduler

# nginx
cp "$APP_DIR/deploy/vm/nginx.conf" /etc/nginx/sites-available/stock-research
ln -sf /etc/nginx/sites-available/stock-research /etc/nginx/sites-enabled/stock-research
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# TLS (skip if no domain provided)
if [ -n "$DOMAIN" ]; then
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@${DOMAIN}"
fi

echo ""
echo "Setup complete."
echo "Next: copy .env to $APP_DIR/.env and run: systemctl restart stock-chat"
if [ -n "$DOMAIN" ]; then
    echo "Then register webhook: python scripts/set_webhook.py https://$DOMAIN/telegram/webhook"
fi

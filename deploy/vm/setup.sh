#!/usr/bin/env bash
# One-shot VM provisioning. Run as root on a fresh Debian/Ubuntu VM.
# Usage: sudo bash setup.sh [your-domain-or-ip]
set -euo pipefail

DOMAIN=${1:-""}
APP_DIR="/opt/stock-research"
APP_USER="stock"
VENV="$APP_DIR/venv"

# System deps (python3 = 3.11 on Debian 12, sufficient for this project)
apt-get update -q
apt-get install -y -q python3 python3-venv git nginx certbot python3-certbot-nginx \
    gcc libxml2-dev libxslt-dev

# App user
useradd -r -m -s /bin/bash "$APP_USER" 2>/dev/null || true

# Allow stock to restart services without password (required by deploy.sh)
cat > /etc/sudoers.d/stock-deploy <<'SUDOERS'
stock ALL=(ALL) NOPASSWD: \
  /bin/systemctl restart stock-chat, \
  /bin/systemctl restart stock-scheduler, \
  /bin/systemctl is-active stock-chat stock-scheduler, \
  /usr/bin/chown -R stock\:stock /opt/stock-research
SUDOERS
chmod 440 /etc/sudoers.d/stock-deploy

# Clone / update repo
if [ -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git -C "$APP_DIR" pull
else
    git clone https://github.com/skarin7/stock-research "$APP_DIR"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

# Virtualenv + deps
sudo -u "$APP_USER" python3 -m venv "$VENV"
sudo -u "$APP_USER" "$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# DB migrations (no-op when DATABASE_URL is unset)
if grep -q "^DATABASE_URL=" "$APP_DIR/.env" 2>/dev/null; then
    sudo -u "$APP_USER" "$VENV/bin/alembic" -c "$APP_DIR/alembic.ini" upgrade head
fi

# systemd services
cp "$APP_DIR/deploy/vm/stock-chat.service" /etc/systemd/system/
cp "$APP_DIR/deploy/vm/stock-scheduler.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable stock-chat
systemctl restart stock-chat
systemctl enable stock-scheduler
systemctl restart stock-scheduler

# TLS — self-signed cert using the VM's public IP (from GCP metadata).
# Telegram accepts self-signed certs when the cert is passed to setWebhook.
# If a domain is provided, use certbot instead for a proper Let's Encrypt cert.
CERT="/etc/ssl/certs/telegram-webhook.crt"
KEY="/etc/ssl/private/telegram-webhook.key"

if [ -n "$DOMAIN" ]; then
    # Let's Encrypt via certbot (requires DNS already pointing to this VM)
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@${DOMAIN}"
else
    # Self-signed cert — generate once, skip if already exists
    if [ ! -f "$CERT" ]; then
        VM_IP=$(curl -sf -H "Metadata-Flavor: Google" \
            http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip)
        openssl req -x509 -newkey rsa:2048 \
            -keyout "$KEY" -out "$CERT" \
            -days 3650 -nodes -subj "/CN=${VM_IP}"
        echo "Self-signed cert generated for IP: $VM_IP"
    fi
fi

# nginx
cp "$APP_DIR/deploy/vm/nginx.conf" /etc/nginx/sites-available/stock-research
ln -sf /etc/nginx/sites-available/stock-research /etc/nginx/sites-enabled/stock-research
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# Print webhook registration command
VM_IP=$(curl -sf -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip \
    2>/dev/null || echo "<vm-ip>")
WEBHOOK_HOST="${DOMAIN:-$VM_IP}"
CERT_FLAG=$([ -z "$DOMAIN" ] && echo " --cert $CERT" || echo "")

echo ""
echo "Setup complete."
echo "Register Telegram webhook (run locally, once):"
echo "  gcloud compute scp stock@\$(hostname):/etc/ssl/certs/telegram-webhook.crt /tmp/telegram-webhook.crt --zone=asia-south1-a"
echo "  python scripts/set_webhook.py https://${WEBHOOK_HOST}/telegram/webhook${CERT_FLAG}"

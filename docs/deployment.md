# Deployment Guide

## Architecture

Single VM runs everything:

```
VM (GCP e2-small or Hetzner CAX11 ~€4/mo)
├── nginx (80/443) → uvicorn 127.0.0.1:8080   ← Telegram webhook
├── systemd: stock-chat    (server/app.py, always-on)
├── systemd: stock-scheduler (scheduler/runner.py, DB-driven cron)
└── Postgres: Neon (external, free tier)
```

Scheduled jobs are stored in the `schedules` DB table (seeded with defaults on first run).
To change a schedule, update the row — no SSH needed.

---

## Prerequisites

- GCP project with billing enabled (or Hetzner account)
- Domain or static IP
- Neon Postgres instance (free tier works): `DATABASE_URL=postgresql://...`
- Telegram bot token + chat ID
- Anthropic API key

---

## 1. Provision VM via Terraform

```bash
cd deploy/terraform

# Copy and fill in your values
cp terraform.tfvars.example terraform.tfvars
# Key fields to set:
#   trading_mode    = "paper"
#   enable_vm       = true
#   vm_ssh_pub_key  = "ssh-rsa AAAA..."   # your public key
#   database_url    = "postgresql://..."
#   telegram_bot_token / telegram_chat_id / telegram_webhook_secret
#   anthropic_api_key

terraform init
terraform apply
```

`terraform output vm_ip` gives the static IP.

---

## 2. First-time setup (runs automatically via startup-script)

Terraform's startup_script runs `deploy/vm/setup.sh` on first boot. It:
1. Clones the repo to `/opt/stock-research`
2. Creates virtualenv + installs deps
3. Writes `.env` from Terraform variables
4. Installs + starts `stock-chat` and `stock-scheduler` systemd services
5. Configures nginx

Allow 2–3 minutes after `terraform apply` for the startup script to finish.

---

## 3. Register Telegram webhook

```bash
# Run once after VM is up
python scripts/set_webhook.py https://<vm-ip-or-domain>/telegram/webhook
```

Or with a domain (after certbot runs):
```bash
python scripts/set_webhook.py https://yourdomain.com/telegram/webhook
```

---

## 4. Verify

```bash
ssh stock@<vm-ip>
systemctl status stock-chat      # should be active
systemctl status stock-scheduler # should be active
journalctl -u stock-chat -f      # live logs
```

Test webhook:
```bash
curl -s https://<vm-ip>/telegram/webhook -X POST \
  -H "X-Telegram-Bot-Api-Secret-Token: <TELEGRAM_WEBHOOK_SECRET>" \
  -d '{"update_id":1}' -o /dev/null -w "%{http_code}"
# expect 200
```

---

## Manage schedules

Default schedules (seeded on first run):

| Name | Mode | Schedule (IST) |
|---|---|---|
| research | research | 06:30 Mon–Fri |
| intraday | intraday | 18:30 Mon–Fri |
| watch | watch | every 3 min Mon–Fri |

To change a schedule, update the `schedules` table directly:
```sql
UPDATE schedules SET cron_expr = '0 7 * * 1-5' WHERE name = 'research';
```

To disable a job temporarily:
```sql
UPDATE schedules SET enabled = false WHERE name = 'watch';
```

---

## TRADING_MODE

Set in `.env` (or Terraform `trading_mode` variable):

| Mode | What happens |
|---|---|
| `off` | Research + chat Q&A only. No trades. |
| `paper` | Simulated fills. Portfolio tracked in DB. |
| `live` | Real Groww orders. HITL approval via Telegram buttons required. |

---

## Cost estimate

| Component | Cost |
|---|---|
| GCP e2-small (asia-south1) | ~$13/mo |
| Hetzner CAX11 (ARM) | ~€4/mo |
| Neon Postgres (free tier) | $0 |
| Anthropic API | ~$1–5/mo depending on usage |

---

## Updates

```bash
ssh stock@<vm-ip>
cd /opt/stock-research
git pull
pip install -r requirements.txt  # if deps changed
systemctl restart stock-chat stock-scheduler
```

locals {
  # All env vars written to /opt/stock-research/.env on the VM.
  # Empties are filtered so optional creds don't create blank entries.
  env_all = merge(
    {
      ANTHROPIC_API_KEY          = var.anthropic_api_key
      DATABASE_URL               = var.database_url
      LANGFUSE_PUBLIC_KEY        = var.langfuse_public_key
      LANGFUSE_SECRET_KEY        = var.langfuse_secret_key
      LANGFUSE_HOST              = var.langfuse_host
      PROMETHEUS_PUSHGATEWAY_URL = var.prometheus_pushgateway_url
      GEMINI_API_KEY             = var.gemini_api_key
      GROWW_ACCESS_TOKEN         = var.groww_access_token
      GROWW_TOTP_TOKEN           = var.groww_totp_token
      GROWW_TOTP_SECRET          = var.groww_totp_secret
      GROWW_TOKEN_ENC_KEY        = var.groww_token_enc_key
      TELEGRAM_BOT_TOKEN         = var.telegram_bot_token
      TELEGRAM_CHAT_ID           = var.telegram_chat_id
      TELEGRAM_WEBHOOK_SECRET    = var.telegram_webhook_secret
      SCREENER_EMAIL             = var.screener_email
      SCREENER_PASSWORD          = var.screener_password
      SCREENER_SCREEN_ID         = var.screener_screen_id
      SCREENER_SCREEN_SLUG       = var.screener_screen_slug
      STOCK_UNIVERSE             = var.stock_universe
      TRADING_MODE               = var.trading_mode
      LLM_PROVIDER               = var.llm_provider
      OPENROUTER_API_KEY         = var.openrouter_api_key
      OPENROUTER_BASE_URL        = var.openrouter_base_url
      OPENROUTER_SCORING_MODEL   = var.openrouter_scoring_model
      OPENROUTER_REPORT_MODEL    = var.openrouter_report_model
      OPENROUTER_CHAT_MODEL      = var.openrouter_chat_model
      TAVILY_API_KEY             = var.tavily_api_key
    },
    var.extra_env,
  )

  env_vars = { for k, v in local.env_all : k => v if v != "" }

  # Rendered .env file written to the VM on first boot.
  # To update config after provisioning: edit /opt/stock-research/.env on the VM
  # and restart services: sudo systemctl restart stock-chat stock-scheduler
  dotenv = join("\n", [for k, v in local.env_vars : "${k}=${v}"])
}

# ── APIs ────────────────────────────────────────────────────────────────────────
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ── Compute Engine VM ─────────────────────────────────────────────────────────

# Instance schedule: VM runs during market hours only to save cost.
# All times UTC (IST = UTC+5:30).
# GCP allows only ONE resource policy per VM instance.
#
#   Start: 00:30 UTC Mon–Fri  (= 06:00 IST)   cron: "30 0 * * 1-5"
#   Stop : 10:30 UTC Mon–Fri  (= 16:00 IST)   cron: "30 10 * * 1-5"

resource "google_compute_resource_policy" "vm_schedule" {
  count  = var.enable_schedule ? 1 : 0
  name   = "${var.job_name}-vm-schedule"
  region = var.region

  instance_schedule_policy {
    vm_start_schedule { schedule = "30 0 * * 1-5"  } # 00:30 UTC Mon-Fri = 06:00 IST
    vm_stop_schedule  { schedule = "30 10 * * 1-5" } # 10:30 UTC Mon-Fri = 16:00 IST
    time_zone = "UTC"
  }
}

resource "google_compute_address" "vm_ip" {
  name   = "${var.job_name}-vm-ip"
  region = var.region
}

resource "google_compute_firewall" "vm_http" {
  name    = "${var.job_name}-vm-http"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80", "443", "22"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["stock-research-vm"]
}

resource "google_compute_instance" "vm" {
  name         = "${var.job_name}-vm"
  machine_type = var.vm_machine_type
  zone         = var.vm_zone
  tags         = ["stock-research-vm"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.vm_ip.address
    }
  }

  metadata = {
    ssh-keys = "stock:${var.vm_ssh_pub_key}"
    "startup-script" = <<-STARTUP
      #!/usr/bin/env bash
      set -euo pipefail
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -q
      apt-get install -y -q git
      if [ ! -d /opt/stock-research/.git ]; then
        git clone https://github.com/skarin7/stock_research /opt/stock-research
      fi
      # Write .env from Terraform vars on first boot only.
      # To push new vars after provisioning: edit .env on VM + restart services.
      if [ ! -f /opt/stock-research/.env ]; then
        cat > /opt/stock-research/.env <<'DOTENV'
${local.dotenv}
DOTENV
      fi
      bash /opt/stock-research/deploy/vm/setup.sh
    STARTUP
  }

  resource_policies = var.enable_schedule ? [
    google_compute_resource_policy.vm_schedule[0].id,
  ] : []

  depends_on = [google_project_service.apis]
}

locals {
  # No app vars here — app config lives in deploy/prod.env, pushed by deploy.sh.
  # Only infra-level locals needed by resources below.
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
      id stock &>/dev/null || useradd -m -s /bin/bash stock
      if [ ! -d /opt/stock-research/.git ]; then
        git clone https://github.com/skarin7/stock_research /opt/stock-research
      fi
      chown -R stock:stock /opt/stock-research
      # .env is pushed by deploy.sh from deploy/prod.env — no bootstrap write here.
      bash /opt/stock-research/deploy/vm/setup.sh
    STARTUP
  }

  resource_policies = var.enable_schedule ? [
    google_compute_resource_policy.vm_schedule[0].id,
  ] : []

  depends_on = [google_project_service.apis]
}

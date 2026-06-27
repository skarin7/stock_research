locals {
  image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_name}/${var.job_name}:${var.image_tag}"

  scheduler_sa = "stock-intelligence-scheduler"
  job_sa       = "stock-intelligence-sa"

  # Cloud Run Admin v1 endpoint to trigger a job execution (matches deploy/setup_gcp.sh).
  job_run_uri = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${var.job_name}:run"

  monitor_job_name = "${var.job_name}-monitor"
  monitor_run_uri  = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${local.monitor_job_name}:run"
  monitor_env      = merge(local.env_vars, { AGENT_PROFILE = "paper" })

  pulse_job_name = "${var.job_name}-pulse"
  pulse_run_uri  = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${local.pulse_job_name}:run"
  # Pulse node is gated by ENABLE_PULSE_AGENT (env flag, not the AGENT_PROFILE set).
  pulse_env = merge(local.env_vars, { ENABLE_PULSE_AGENT = "true" })

  # All env vars on the job. Empties are filtered out so optional creds don't
  # create blank env entries. The app reads these via config.py.
  chat_service_name = var.chat_service_name
  chat_env = merge(local.env_vars, {
    ENABLE_CHAT_AGENT       = "true"
    TELEGRAM_WEBHOOK_SECRET = var.telegram_webhook_secret
  })

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
      TELEGRAM_BOT_TOKEN         = var.telegram_bot_token
      TELEGRAM_CHAT_ID           = var.telegram_chat_id
      SCREENER_EMAIL             = var.screener_email
      SCREENER_PASSWORD          = var.screener_password
      SCREENER_SCREEN_ID         = var.screener_screen_id
      SCREENER_SCREEN_SLUG       = var.screener_screen_slug
      STOCK_UNIVERSE             = var.stock_universe
      AGENT_PROFILE              = var.agent_profile
      LLM_PROVIDER               = var.llm_provider
      OPENROUTER_API_KEY         = var.openrouter_api_key
      OPENROUTER_SCORING_MODEL   = var.openrouter_scoring_model
      OPENROUTER_REPORT_MODEL    = var.openrouter_report_model
    },
    var.extra_env,
  )

  env_vars = { for k, v in local.env_all : k => v if v != "" }
}

# ── APIs ────────────────────────────────────────────────────────────────────────
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ── Artifact Registry ─────────────────────────────────────────────────────────
# If the repo already exists (created by deploy/setup_gcp.sh), import it once:
#   terraform import google_artifact_registry_repository.repo \
#     projects/<project>/locations/<region>/repositories/<repo_name>
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = var.repo_name
  format        = "DOCKER"
  description   = "Stock intelligence container images"

  depends_on = [google_project_service.apis]
}

# ── Service accounts ──────────────────────────────────────────────────────────
resource "google_service_account" "job" {
  account_id   = local.job_sa
  display_name = "Stock Intelligence Cloud Run"
}

resource "google_service_account" "scheduler" {
  account_id   = local.scheduler_sa
  display_name = "Stock Intelligence Scheduler"
}

# ── Cloud Run Job ─────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_job" "job" {
  name     = var.job_name
  location = var.region

  deletion_protection = false

  template {
    template {
      service_account = google_service_account.job.email
      timeout         = var.task_timeout
      max_retries     = 1

      containers {
        image   = local.image
        command = var.job_command
        args    = var.job_args

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }

        dynamic "env" {
          for_each = local.env_vars
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Let the scheduler SA trigger this job.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.job.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# ── Cloud Scheduler (daily trigger) ───────────────────────────────────────────
resource "google_cloud_scheduler_job" "daily" {
  name      = "${var.job_name}-daily"
  region    = var.region
  schedule  = var.schedule
  time_zone = var.time_zone

  http_target {
    http_method = "POST"
    uri         = local.job_run_uri
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [
    google_project_service.apis,
    google_cloud_run_v2_job_iam_member.scheduler_invoker,
  ]
}

# ── Monitoring job + market-hours scheduler (opt-in) ──────────────────────────
resource "google_cloud_run_v2_job" "monitor" {
  count               = var.enable_monitoring ? 1 : 0
  name                = local.monitor_job_name
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.job.email
      timeout         = "600s"
      max_retries     = 0

      containers {
        image   = local.image
        command = var.job_command
        args    = ["run_agents.py", "--mode", "monitor"]

        resources {
          limits = { cpu = "1", memory = var.memory }
        }

        dynamic "env" {
          for_each = local.monitor_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job_iam_member" "monitor_invoker" {
  count    = var.enable_monitoring ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.monitor[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# ── Chat agent Cloud Run Service (Telegram webhook) ───────────────────────────
resource "google_cloud_run_v2_service" "chat" {
  count    = var.enable_chat_agent ? 1 : 0
  name     = local.chat_service_name
  location = var.region

  deletion_protection = false

  template {
    service_account = google_service_account.job.email

    timeout = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image   = local.image
      command = ["python"]
      args    = ["-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8080"]
      ports { container_port = 8080 }

      resources {
        limits = { cpu = "1", memory = var.memory }
      }

      dynamic "env" {
        for_each = local.chat_env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Allow unauthenticated invocations (Telegram sends plain HTTPS).
resource "google_cloud_run_v2_service_iam_member" "chat_public" {
  count    = var.enable_chat_agent ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.chat[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_scheduler_job" "monitor" {
  count     = var.enable_monitoring ? 1 : 0
  name      = "${local.monitor_job_name}-cron"
  region    = var.region
  schedule  = var.monitor_schedule
  time_zone = var.time_zone

  http_target {
    http_method = "POST"
    uri         = local.monitor_run_uri
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [
    google_project_service.apis,
    google_cloud_run_v2_job_iam_member.monitor_invoker,
  ]
}

# ── Market-pulse shock watcher (tight cadence, incl. pre-open) ────────────────
resource "google_cloud_run_v2_job" "pulse" {
  count               = var.enable_pulse ? 1 : 0
  name                = local.pulse_job_name
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.job.email
      timeout         = "120s"
      max_retries     = 0

      containers {
        image   = local.image
        command = var.job_command
        args    = ["run_agents.py", "--mode", "pulse"]

        resources {
          limits = { cpu = "1", memory = var.memory }
        }

        dynamic "env" {
          for_each = local.pulse_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job_iam_member" "pulse_invoker" {
  count    = var.enable_pulse ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.pulse[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "pulse" {
  count     = var.enable_pulse ? 1 : 0
  name      = "${local.pulse_job_name}-cron"
  region    = var.region
  schedule  = var.pulse_schedule
  time_zone = var.time_zone

  http_target {
    http_method = "POST"
    uri         = local.pulse_run_uri
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [
    google_project_service.apis,
    google_cloud_run_v2_job_iam_member.pulse_invoker,
  ]
}

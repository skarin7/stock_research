locals {
  image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_name}/${var.job_name}:${var.image_tag}"

  scheduler_sa = "stock-intelligence-scheduler"
  job_sa       = "stock-intelligence-sa"

  # Cloud Run Admin v1 endpoint to trigger a job execution (matches deploy/setup_gcp.sh).
  job_run_uri = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${var.job_name}:run"

  # All env vars on the job. Empties are filtered out so optional creds don't
  # create blank env entries. The app reads these via config.py.
  env_all = merge(
    {
      ANTHROPIC_API_KEY    = var.anthropic_api_key
      DATABASE_URL         = var.database_url
      LANGFUSE_PUBLIC_KEY  = var.langfuse_public_key
      LANGFUSE_SECRET_KEY  = var.langfuse_secret_key
      LANGFUSE_HOST        = var.langfuse_host
      GEMINI_API_KEY       = var.gemini_api_key
      GROWW_TOTP_TOKEN     = var.groww_totp_token
      GROWW_TOTP_SECRET    = var.groww_totp_secret
      TELEGRAM_BOT_TOKEN   = var.telegram_bot_token
      TELEGRAM_CHAT_ID     = var.telegram_chat_id
      SCREENER_EMAIL       = var.screener_email
      SCREENER_PASSWORD    = var.screener_password
      SCREENER_SCREEN_ID   = var.screener_screen_id
      SCREENER_SCREEN_SLUG = var.screener_screen_slug
      STOCK_UNIVERSE       = var.stock_universe
      AGENT_MODE           = var.agent_mode
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

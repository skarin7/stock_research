output "image" {
  value       = local.image
  description = "Full image path the job runs."
}

output "job_name" {
  value = google_cloud_run_v2_job.job.name
}

output "schedule" {
  value       = "${var.schedule} (${var.time_zone})"
  description = "Cron schedule for the daily run."
}

output "run_manually" {
  value       = "gcloud run jobs execute ${var.job_name} --region=${var.region} --wait"
  description = "Trigger a run on demand."
}

output "view_logs" {
  value = "gcloud run jobs executions list --job=${var.job_name} --region=${var.region}"
}

output "durable_state" {
  value       = var.database_url == "" ? "NO DATABASE_URL set — LangGraph falls back to in-memory (no resumable runs / no trade-approval persistence). Set database_url to your Neon connection string." : "Postgres configured."
  description = "Whether durable agent/trading state is wired up."
}

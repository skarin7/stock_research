# ── GCP target ────────────────────────────────────────────────────────────────
variable "project_id" {
  type        = string
  description = "GCP project ID."
  default     = "gen-lang-client-0533266855"
}

variable "region" {
  type        = string
  description = "GCP region (asia-south1 = Mumbai, closest to NSE/BSE)."
  default     = "asia-south1"
}

variable "job_name" {
  type        = string
  description = "Cloud Run Job name (also the Artifact Registry image name)."
  default     = "stock-intelligence"
}

variable "repo_name" {
  type        = string
  description = "Artifact Registry repository name."
  default     = "stock-intelligence"
}

variable "image_tag" {
  type        = string
  description = "Image tag to deploy. deploy.sh builds and pushes this tag."
  default     = "latest"
}

# ── Scheduling + sizing ─────────────────────────────────────────────────────────
variable "schedule" {
  type        = string
  description = "Cron schedule (UTC). Default 1:30 AM UTC = 7:00 AM IST, Mon–Fri."
  default     = "30 1 * * 1-5"
}

variable "time_zone" {
  type    = string
  default = "UTC"
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "1Gi"
}

variable "task_timeout" {
  type    = string
  default = "3600s"
}

# Container entrypoint. Defaults to the agent system in research mode (report only,
# no trading). Override to ["main.py", "--skip-backtest"] to run the legacy pipeline.
variable "job_command" {
  type    = list(string)
  default = ["python"]
}

variable "job_args" {
  type    = list(string)
  default = ["run_agents.py", "--mode", "research"]
}

# ── Non-secret config (plain env on the job) ────────────────────────────────────
variable "stock_universe" {
  type    = string
  default = "nifty200"
}

variable "agent_mode" {
  type    = string
  default = "research" # research | paper | live
}

# ── LLM provider (anthropic default; openrouter for cheap models) ───────────────
variable "llm_provider" {
  type    = string
  default = "anthropic" # anthropic | openrouter
}

variable "openrouter_scoring_model" {
  type    = string
  default = "deepseek/deepseek-chat"
}

variable "openrouter_report_model" {
  type    = string
  default = "deepseek/deepseek-chat"
}

variable "openrouter_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "langfuse_host" {
  type        = string
  description = "Langfuse Cloud host (e.g. https://cloud.langfuse.com or https://us.cloud.langfuse.com)."
  default     = "https://cloud.langfuse.com"
}

# Extra plain env vars (e.g. agent feature flags, risk limits). Merged last.
variable "extra_env" {
  type    = map(string)
  default = {}
}

# ── Secrets / credentials (injected as env vars; mark sensitive) ────────────────
# Supply via terraform.tfvars (gitignored) or TF_VAR_* environment variables.
# NOTE: these land in Terraform state — keep state private (local file or a
# locked-down GCS bucket). This mirrors the existing plain-env model (no Secret
# Manager) chosen to keep cost near zero.
variable "anthropic_api_key" {
  type      = string
  sensitive = true
}

variable "database_url" {
  type        = string
  sensitive   = true
  description = "Neon (or any) Postgres connection string for agent + trading state. Empty → MemorySaver fallback (no durable checkpoints)."
  default     = ""
}

variable "langfuse_public_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "langfuse_secret_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "gemini_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "groww_totp_token" {
  type      = string
  sensitive = true
  default   = ""
}

variable "groww_totp_secret" {
  type      = string
  sensitive = true
  default   = ""
}

variable "telegram_bot_token" {
  type      = string
  sensitive = true
  default   = ""
}

variable "telegram_chat_id" {
  type      = string
  sensitive = true
  default   = ""
}

variable "screener_email" {
  type      = string
  sensitive = true
  default   = ""
}

variable "screener_password" {
  type      = string
  sensitive = true
  default   = ""
}

variable "screener_screen_id" {
  type    = string
  default = ""
}

variable "screener_screen_slug" {
  type    = string
  default = ""
}

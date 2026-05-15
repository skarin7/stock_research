#!/usr/bin/env bash
# One-time GCP setup for Stock Intelligence pipeline.
# Run this once from your laptop. After that, Cloud Scheduler fires daily.
#
# Prerequisites:
#   gcloud CLI installed and authenticated (gcloud auth login)
#   Docker installed and running
#   .env file filled in at stock-intelligence/.env
#
# Usage:
#   cd stock-intelligence
#   bash deploy/setup_gcp.sh

set -euo pipefail

SCRIPT_START=$(date +%s)

# ── CONFIG — edit these two lines ────────────────────────────────────────────
PROJECT_ID="gen-lang-client-0533266855"        # gcloud projects list
REGION="asia-south1"                     # Mumbai — closest to India
# ─────────────────────────────────────────────────────────────────────────────

JOB_NAME="stock-intelligence"
REPO_NAME="stock-intelligence"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$JOB_NAME:latest"
SA_NAME="stock-intelligence-sa"
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
SCHEDULER_SA_NAME="stock-intelligence-scheduler"
SCHEDULER_SA_EMAIL="$SCHEDULER_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

# Sensitive values → Secret Manager (never change without a new secret version)
SECRETS=(
  ANTHROPIC_API_KEY
  GROWW_TOTP_TOKEN
  GROWW_TOTP_SECRET
  GEMINI_API_KEY
  SCREENER_EMAIL
  SCREENER_PASSWORD
  SCREENER_SCREEN_ID
  SCREENER_SCREEN_SLUG
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
)

# Non-sensitive config → plain env vars on the Cloud Run job
# Change these anytime with:
#   gcloud run jobs update stock-intelligence --region=asia-south1 \
#     --update-env-vars KEY=VALUE
CONFIG_VARS=(
  STOCK_UNIVERSE=nifty200
)

log() { echo -e "\n\033[1;32m▶ $*\033[0m"; }
err() { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

# ── 0. Validate ───────────────────────────────────────────────────────────────
[[ "$PROJECT_ID" == "your-gcp-project-id" ]] && err "Set PROJECT_ID at the top of this script first."
[[ -f ".env" ]] || err "Run this script from the stock-intelligence/ directory (no .env found)."
command -v gcloud >/dev/null || err "gcloud CLI not installed."
command -v docker  >/dev/null || err "Docker not installed."

log "Setting project to $PROJECT_ID"
gcloud config set project "$PROJECT_ID"

# ── 1. Enable APIs ────────────────────────────────────────────────────────────
log "Enabling required GCP APIs"
gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  --quiet

# ── 2. Artifact Registry repo ─────────────────────────────────────────────────
log "Creating Artifact Registry repository"
gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" \
  --quiet 2>/dev/null || echo "  (already exists)"

gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet

# ── 3. Service accounts ───────────────────────────────────────────────────────
log "Creating service account for Cloud Run job"
gcloud iam service-accounts create "$SA_NAME" \
  --display-name="Stock Intelligence Cloud Run" \
  --quiet 2>/dev/null || echo "  (already exists)"

log "Creating service account for Cloud Scheduler"
gcloud iam service-accounts create "$SCHEDULER_SA_NAME" \
  --display-name="Stock Intelligence Scheduler" \
  --quiet 2>/dev/null || echo "  (already exists)"

# ── 4. Push secrets from .env → Secret Manager ───────────────────────────────
log "Loading secrets from .env into Secret Manager"
# Source .env using sed to strip comments/blanks, then export
# Using sed+cut avoids IFS='=' issues with long values containing multiple '='
while IFS= read -r line; do
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line//[[:space:]]/}" ]] && continue
  key="${line%%=*}"
  value="${line#*=}"
  [[ -z "$key" ]] && continue
  export "$key"="$value"
done < .env

for SECRET_NAME in "${SECRETS[@]}"; do
  value="${!SECRET_NAME:-}"
  [[ -z "$value" ]] && { echo "  ⚠ $SECRET_NAME not set in .env — skipping"; continue; }

  # Create secret if it doesn't exist
  gcloud secrets describe "$SECRET_NAME" --quiet 2>/dev/null || \
    gcloud secrets create "$SECRET_NAME" --replication-policy="automatic" --quiet

  # Add new version
  echo -n "$value" | gcloud secrets versions add "$SECRET_NAME" --data-file=- --quiet
  echo "  ✓ $SECRET_NAME"
done

# Grant Cloud Run SA access to all secrets
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet

# ── 5. Build and push Docker image via Cloud Build (no local Docker needed) ───
log "Building and pushing image via Cloud Build (Kaniko cache enabled)"
gcloud builds submit --config cloudbuild.yaml .

# ── 6. Create Cloud Run Job ───────────────────────────────────────────────────
log "Creating Cloud Run Job"

# Build --set-secrets flag
SECRET_FLAGS=""
for SECRET_NAME in "${SECRETS[@]}"; do
  SECRET_FLAGS="$SECRET_FLAGS --set-secrets=${SECRET_NAME}=${SECRET_NAME}:latest"
done

# Build --set-env-vars flag (comma-separated KEY=VALUE pairs)
ENV_VAR_LIST=$(IFS=,; echo "${CONFIG_VARS[*]}")
ENV_FLAGS="--set-env-vars=${ENV_VAR_LIST}"

gcloud run jobs create "$JOB_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$SA_EMAIL" \
  --memory="1Gi" \
  --cpu="1" \
  --task-timeout="3600s" \
  --max-retries=1 \
  $SECRET_FLAGS \
  $ENV_FLAGS \
  --quiet 2>/dev/null || \
gcloud run jobs update "$JOB_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$SA_EMAIL" \
  --memory="1Gi" \
  --cpu="1" \
  --task-timeout="3600s" \
  --max-retries=1 \
  $SECRET_FLAGS \
  $ENV_FLAGS \
  --quiet

# ── 7. Grant Scheduler permission to trigger the job ─────────────────────────
log "Granting scheduler permission to invoke Cloud Run job"
gcloud run jobs add-iam-policy-binding "$JOB_NAME" \
  --region="$REGION" \
  --member="serviceAccount:$SCHEDULER_SA_EMAIL" \
  --role="roles/run.invoker" \
  --quiet

# ── 8. Create Cloud Scheduler job ────────────────────────────────────────────
log "Creating Cloud Scheduler cron (7:00 AM IST = 1:30 AM UTC, Mon–Fri)"
JOB_RUN_URI="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$JOB_NAME:run"

gcloud scheduler jobs create http "${JOB_NAME}-daily" \
  --location="$REGION" \
  --schedule="30 1 * * 1-5" \
  --uri="$JOB_RUN_URI" \
  --message-body="{}" \
  --oauth-service-account-email="$SCHEDULER_SA_EMAIL" \
  --time-zone="UTC" \
  --quiet 2>/dev/null || \
gcloud scheduler jobs update http "${JOB_NAME}-daily" \
  --location="$REGION" \
  --schedule="30 1 * * 1-5" \
  --uri="$JOB_RUN_URI" \
  --message-body="{}" \
  --oauth-service-account-email="$SCHEDULER_SA_EMAIL" \
  --quiet

# ── Done ──────────────────────────────────────────────────────────────────────
log "Setup complete!"
echo ""
echo "  Image:     $IMAGE"
echo "  Job:       $JOB_NAME ($REGION)"
echo "  Schedule:  Mon–Fri 7:00 AM IST (1:30 AM UTC)"
echo ""
echo "  Run manually:  gcloud run jobs execute $JOB_NAME --region=$REGION --wait"
echo "  View logs:     gcloud run jobs executions list --job=$JOB_NAME --region=$REGION"
echo ""
echo "  To update after code changes:"
echo "    gcloud builds submit --config cloudbuild.yaml . && gcloud run jobs update $JOB_NAME --image=$IMAGE --region=$REGION"
echo ""
ELAPSED=$(( $(date +%s) - SCRIPT_START ))
echo "  Completed in ${ELAPSED}s ($(( ELAPSED / 60 ))m $(( ELAPSED % 60 ))s)"

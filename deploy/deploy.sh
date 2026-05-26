#!/usr/bin/env bash
# Single deploy entrypoint for the stock-intelligence agent system.
#
# Provisions everything (Artifact Registry, Cloud Run Job, Cloud Scheduler, service
# accounts) via Terraform, builds + pushes the image, and wires Neon Postgres +
# Langfuse Cloud creds onto the job. Managed/serverless — nothing runs 24/7.
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth login)
#   - terraform >= 1.5 installed
#   - deploy/terraform/terraform.tfvars filled in (copy from terraform.tfvars.example)
#
# Usage:
#   bash deploy/deploy.sh            # build + provision/update everything
#   bash deploy/deploy.sh --plan     # show terraform plan only, no changes
#   bash deploy/deploy.sh --run      # deploy, then trigger one run immediately

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$REPO_ROOT/deploy/terraform"
cd "$TF_DIR"

log() { echo -e "\n\033[1;32m▶ $*\033[0m"; }

PLAN_ONLY=false
RUN_AFTER=false
for arg in "$@"; do
  case "$arg" in
    --plan) PLAN_ONLY=true ;;
    --run)  RUN_AFTER=true ;;
    *) echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

[[ -f terraform.tfvars ]] || {
  echo "✗ deploy/terraform/terraform.tfvars not found." >&2
  echo "  cp deploy/terraform/terraform.tfvars.example deploy/terraform/terraform.tfvars and fill it in." >&2
  exit 1
}

log "terraform init"
terraform init -input=false

if $PLAN_ONLY; then
  log "terraform plan"
  terraform plan
  exit 0
fi

# Read project/region/repo/job from terraform vars (fall back to module defaults).
PROJECT_ID="$(terraform console <<<'var.project_id' | tr -d '"')"
REGION="$(terraform console <<<'var.region' | tr -d '"')"
REPO_NAME="$(terraform console <<<'var.repo_name' | tr -d '"')"
JOB_NAME="$(terraform console <<<'var.job_name' | tr -d '"')"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$JOB_NAME:latest"

gcloud config set project "$PROJECT_ID" --quiet

# Phase 1 — create the registry (+ APIs) BEFORE building, since the image is
# pushed into it and the Cloud Run Job won't create without a pullable image.
log "Phase 1/3: provision APIs + Artifact Registry"
terraform apply -input=false -auto-approve \
  -target=google_project_service.apis \
  -target=google_artifact_registry_repository.repo

# Phase 2 — build + push the image via Cloud Build (no local Docker needed).
log "Phase 2/3: build + push image ($IMAGE)"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
( cd "$REPO_ROOT" && gcloud builds submit --config cloudbuild.yaml . )

# Phase 3 — provision the rest (job, scheduler, IAM) now that the image exists.
log "Phase 3/3: provision Cloud Run Job + Scheduler"
terraform apply -input=false -auto-approve

log "Done."
terraform output

if $RUN_AFTER; then
  log "Triggering a run now"
  gcloud run jobs execute "$JOB_NAME" --region="$REGION" --wait
fi

#!/usr/bin/env bash
# Adopt resources previously created by deploy/setup_gcp.sh (gcloud) into the
# Terraform state, so `terraform apply` manages them instead of failing with
# "already exists". Zero cost — no resources are created or destroyed here.
#
# Run from deploy/terraform/ AFTER `terraform init` and filling terraform.tfvars.
# Idempotent: skips anything already in state. Review `terraform plan` afterwards
# and reconcile any drift before applying.
#
#   bash import.sh

set -euo pipefail
cd "$(dirname "$0")"

PROJECT="${PROJECT_ID:-gen-lang-client-0533266855}"
REGION="${REGION:-asia-south1}"
JOB="${JOB_NAME:-stock-intelligence}"
REPO="${REPO_NAME:-stock-intelligence}"
SA_JOB="stock-intelligence-sa@${PROJECT}.iam.gserviceaccount.com"
SA_SCHED="stock-intelligence-scheduler@${PROJECT}.iam.gserviceaccount.com"

imp() {  # imp <tf-address> <import-id>
  if terraform state list 2>/dev/null | grep -qxF "$1"; then
    echo "  already in state: $1"
  else
    echo "  importing: $1"
    terraform import "$1" "$2"
  fi
}

echo "Importing existing GCP resources into Terraform state..."

for s in run.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com; do
  imp "google_project_service.apis[\"$s\"]" "$PROJECT/$s"
done

imp "google_artifact_registry_repository.repo" "projects/$PROJECT/locations/$REGION/repositories/$REPO"
imp "google_service_account.job" "projects/$PROJECT/serviceAccounts/$SA_JOB"
imp "google_service_account.scheduler" "projects/$PROJECT/serviceAccounts/$SA_SCHED"
imp "google_cloud_run_v2_job.job" "projects/$PROJECT/locations/$REGION/jobs/$JOB"
imp "google_cloud_scheduler_job.daily" "projects/$PROJECT/locations/$REGION/jobs/${JOB}-daily"
imp "google_cloud_run_v2_job_iam_member.scheduler_invoker" \
    "projects/$PROJECT/locations/$REGION/jobs/$JOB roles/run.invoker serviceAccount:$SA_SCHED"

echo
echo "Done. Now run:  terraform plan"
echo "Review the diff (env vars / settings may differ from the gcloud setup) before apply."

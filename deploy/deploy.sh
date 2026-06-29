#!/usr/bin/env bash
# Deploy stock-intelligence to the GCP VM.
#
# Auto-detects whether infra changes are needed:
#   - If any *.tf / terraform.tfvars changed since last apply → terraform apply first
#   - Always SSH into the VM, git pull, restart services
#
# Flags:
#   --no-schedule   disable market-hours VM schedule (VM stays up — useful for testing)
#   --plan          terraform plan only, no changes applied
#
# Usage:
#   bash deploy/deploy.sh              # smart deploy (auto terraform if needed + code)
#   bash deploy/deploy.sh --no-schedule
#   bash deploy/deploy.sh --plan

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$REPO_ROOT/deploy/terraform"
cd "$TF_DIR"

log() { echo -e "\n\033[1;32m▶ $*\033[0m"; }

PLAN_ONLY=false
EXTRA_TF_VARS=""

for arg in "$@"; do
  case "$arg" in
    --no-schedule) EXTRA_TF_VARS="-var=enable_schedule=false" ;;
    --plan)        PLAN_ONLY=true ;;
    *) echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

[[ -f terraform.tfvars ]] || {
  echo "✗ deploy/terraform/terraform.tfvars not found." >&2
  echo "  cp deploy/terraform/terraform.tfvars.example deploy/terraform/terraform.tfvars" >&2
  exit 1
}

terraform init -input=false -upgrade=false > /dev/null

PROJECT_ID="$(terraform console <<<'var.project_id' | tr -d '"')"
JOB_NAME="$(terraform console <<<'var.job_name' | tr -d '"')"
VM_ZONE="$(terraform console <<<'var.vm_zone' | tr -d '"')"
VM_NAME="${JOB_NAME}-vm"

gcloud config set project "$PROJECT_ID" --quiet

# ── Plan only ─────────────────────────────────────────────────────────────────
if $PLAN_ONLY; then
  log "terraform plan"
  terraform plan $EXTRA_TF_VARS
  exit 0
fi

# ── Detect if terraform apply is needed ───────────────────────────────────────
# Triggers: no state, .tf/.tfvars newer than state, OR VM doesn't exist in GCP.
VM_EXISTS=$(gcloud compute instances describe "$VM_NAME" --zone="$VM_ZONE" \
  --format="value(status)" 2>/dev/null || echo "NOT_FOUND")

TF_NEEDED=false
if [[ ! -f terraform.tfstate ]]; then
  TF_NEEDED=true
  log "No terraform state — will provision VM"
elif [[ "$VM_EXISTS" == "NOT_FOUND" ]]; then
  TF_NEEDED=true
  log "VM not found in GCP — will provision"
elif find "$TF_DIR" -maxdepth 1 \( -name "*.tf" -o -name "terraform.tfvars" \) \
    -newer terraform.tfstate | grep -q .; then
  TF_NEEDED=true
  log "Terraform files changed — will apply infra changes"
else
  log "No changes detected — skipping terraform (code-only deploy)"
fi

# ── Phase 1: Terraform (when needed) ──────────────────────────────────────────
if $TF_NEEDED; then
  log "terraform apply"
  terraform apply -input=false -auto-approve $EXTRA_TF_VARS
  terraform output
fi

# ── Phase 2: Code deploy ───────────────────────────────────────────────────────
log "Code deploy → $VM_NAME ($VM_ZONE)"

# Re-check status after terraform may have just created the VM.
STATUS=$(gcloud compute instances describe "$VM_NAME" --zone="$VM_ZONE" \
  --format="value(status)" 2>/dev/null || echo "NOT_FOUND")

if [[ "$STATUS" == "NOT_FOUND" ]]; then
  echo "✗ VM $VM_NAME still not found — terraform may have failed." >&2
  exit 1
fi

if [[ "$STATUS" != "RUNNING" ]]; then
  log "VM is $STATUS — starting it"
  gcloud compute instances start "$VM_NAME" --zone="$VM_ZONE"
  echo "Waiting for SSH..."
  sleep 20
fi

gcloud compute ssh "stock@${VM_NAME}" --zone="$VM_ZONE" --command="
  set -euo pipefail
  cd /opt/stock-research
  echo '--- git pull ---'
  git pull
  echo '--- restarting services ---'
  sudo systemctl restart stock-chat stock-scheduler
  echo '--- status ---'
  sudo systemctl is-active stock-chat stock-scheduler
"

log "Done. Code deployed + services restarted on $VM_NAME."

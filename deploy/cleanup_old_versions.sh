#!/usr/bin/env bash
# One-time cleanup: delete existing Secret Manager secrets and untagged
# Artifact Registry images. Safe to re-run (idempotent).
#
# Run this ONCE after migrating setup_gcp.sh to env-vars-only mode.
#
# Usage:
#   bash deploy/cleanup_old_versions.sh

set -euo pipefail

PROJECT_ID="gen-lang-client-0533266855"
REGION="asia-south1"
REPO_NAME="stock-intelligence"
IMAGE_PATH="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/stock-intelligence"

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

log() { echo -e "\n\033[1;32m▶ $*\033[0m"; }

gcloud config set project "$PROJECT_ID" --quiet

# ── 1. Delete all Secret Manager secrets ─────────────────────────────────────
log "Deleting Secret Manager secrets (all versions)"
for s in "${SECRETS[@]}"; do
  if gcloud secrets describe "$s" --quiet >/dev/null 2>&1; then
    gcloud secrets delete "$s" --quiet
    echo "  ✓ deleted $s"
  else
    echo "  (already gone) $s"
  fi
done

# ── 2. Delete all untagged Artifact Registry images ──────────────────────────
log "Deleting untagged Artifact Registry images"
UNTAGGED=$(gcloud artifacts docker images list "$IMAGE_PATH" \
  --include-tags --filter="-tags:*" \
  --format="value(version)" 2>/dev/null || true)

if [[ -z "$UNTAGGED" ]]; then
  echo "  (no untagged images)"
else
  while IFS= read -r digest; do
    [[ -z "$digest" ]] && continue
    gcloud artifacts docker images delete "$IMAGE_PATH@$digest" --quiet --delete-tags
    echo "  ✓ deleted $digest"
  done <<< "$UNTAGGED"
fi

# ── 3. Delete tagged images other than :latest ───────────────────────────────
log "Deleting tagged images other than :latest"
OTHER_TAGS=$(gcloud artifacts docker images list "$IMAGE_PATH" \
  --include-tags --format="value(version,tags)" 2>/dev/null \
  | awk '$2 != "latest" && $2 != "" {print $1}' || true)

if [[ -z "$OTHER_TAGS" ]]; then
  echo "  (none)"
else
  while IFS= read -r digest; do
    [[ -z "$digest" ]] && continue
    gcloud artifacts docker images delete "$IMAGE_PATH@$digest" --quiet --delete-tags
    echo "  ✓ deleted $digest"
  done <<< "$OTHER_TAGS"
fi

log "Cleanup complete"
echo ""
echo "  Remaining images:"
gcloud artifacts docker images list "$IMAGE_PATH" --include-tags --format="table(version,tags,createTime.date())" 2>/dev/null || echo "  (none)"

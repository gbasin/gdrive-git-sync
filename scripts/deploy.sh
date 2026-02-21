#!/usr/bin/env bash
set -euo pipefail

# Deploy: source .env, generate tfvars, zip functions, terraform apply.
# Called standalone (make deploy) or from setup.sh.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT_DIR/.env"
TFVARS_FILE="$ROOT_DIR/infra/terraform.tfvars"

cleanup() {
  local rc=$?
  [ $rc -eq 0 ] && return
  echo ""
  echo "❌ Deploy failed — check the error above."
  echo "   Fix the issue and re-run: make deploy"
}
trap cleanup EXIT

# ── Source .env ──────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "Error: .env not found."
  echo "Run 'make setup' for guided first-time config, or copy .env.example to .env."
  exit 1
fi

: "${GCP_PROJECT:?GCP_PROJECT is required in .env}"
: "${DRIVE_FOLDER_ID:?DRIVE_FOLDER_ID is required in .env}"
: "${GIT_REPO_URL:?GIT_REPO_URL is required in .env}"

# ── Generate terraform.tfvars ────────────────────────────────────────
echo "=== Generating terraform.tfvars ==="

cat > "$TFVARS_FILE" <<EOF
# Auto-generated from .env — do not edit directly.
gcp_project             = "${GCP_PROJECT}"
drive_folder_id         = "${DRIVE_FOLDER_ID}"
git_repo_url            = "${GIT_REPO_URL}"
git_branch              = "${GIT_BRANCH:-main}"
git_token_secret        = "${GIT_TOKEN_SECRET:-git-token}"
functions_source_bucket = "${GCP_PROJECT}-functions-source"
EOF

# Optional vars — only written when set
[ -n "${EXCLUDE_PATHS:-}" ]             && echo "exclude_paths             = \"${EXCLUDE_PATHS}\""             >> "$TFVARS_FILE"
[ -n "${SKIP_EXTENSIONS:-}" ]           && echo "skip_extensions           = \"${SKIP_EXTENSIONS}\""           >> "$TFVARS_FILE"
[ -n "${MAX_FILE_SIZE_MB:-}" ]          && echo "max_file_size_mb          = ${MAX_FILE_SIZE_MB}"              >> "$TFVARS_FILE"
[ -n "${COMMIT_AUTHOR_NAME:-}" ]        && echo "commit_author_name        = \"${COMMIT_AUTHOR_NAME}\""       >> "$TFVARS_FILE"
[ -n "${COMMIT_AUTHOR_EMAIL:-}" ]       && echo "commit_author_email       = \"${COMMIT_AUTHOR_EMAIL}\""      >> "$TFVARS_FILE"
[ -n "${FIRESTORE_COLLECTION:-}" ]      && echo "firestore_collection      = \"${FIRESTORE_COLLECTION}\""     >> "$TFVARS_FILE"
[ -n "${DOCS_SUBDIR:-}" ]               && echo "docs_subdir               = \"${DOCS_SUBDIR}\""              >> "$TFVARS_FILE"
[ -n "${GOOGLE_VERIFICATION_TOKEN:-}" ] && echo "google_verification_token = \"${GOOGLE_VERIFICATION_TOKEN}\"" >> "$TFVARS_FILE"

echo "  → $TFVARS_FILE"

# ── Package functions ────────────────────────────────────────────────
echo "=== Packaging functions ==="
cd "$ROOT_DIR"

rm -f functions_source.zip
cd functions
zip -r ../functions_source.zip . -x '__pycache__/*' '*.pyc'
cd "$ROOT_DIR"

# ── Check GCP auth ───────────────────────────────────────────────────
if ! gcloud auth application-default print-access-token &>/dev/null; then
  echo "⚠  GCP credentials expired or missing."
  if [ -n "${CI:-}" ] || [ -n "${NONINTERACTIVE:-}" ]; then
    echo "   Run: gcloud auth application-default login"
    exit 1
  fi
  echo "   Re-authenticating…"
  gcloud auth application-default login
fi

# ── Terraform ────────────────────────────────────────────────────────
echo "=== Running Terraform ==="
cd infra

terraform init -input=false
terraform apply -auto-approve -input=false

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Sync handler URL: $(terraform output -raw sync_handler_url)"
echo "Service account:  $(terraform output -raw service_account_email)"
echo ""
echo "Next steps:"
echo "  1. Share your Drive folder with the service account above"
echo "  2. Complete domain verification (if not done)"
echo "  3. Initialize: curl -X POST $(terraform output -raw setup_watch_url)"

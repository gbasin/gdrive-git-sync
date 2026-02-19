#!/usr/bin/env bash
set -euo pipefail

# Interactive setup for gdrive-git-sync
# Idempotent — safe to re-run at any point.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT_DIR/.env"
TFVARS_FILE="$ROOT_DIR/infra/terraform.tfvars"

# ── Colors & helpers ─────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

ok()   { printf "  ${GREEN}✔${NC} %s\n" "$*"; }
fail() { printf "  ${RED}✘${NC} %s\n" "$*"; }
info() { printf "  ${BLUE}▸${NC} %s\n" "$*"; }
hint() { printf "    ${DIM}%s${NC}\n" "$*"; }

phase() {
  echo ""
  printf "${BOLD}[$1] $2${NC}\n"
}

# Spinner — runs a command in the background, shows elapsed time.
# On success: ✔ message (Xs). On failure: ✘ message + last 20 lines.
spin() {
  local msg="$1"; shift
  local logfile; logfile=$(mktemp)
  local rc=0 s=0

  "$@" >"$logfile" 2>&1 &
  local pid=$!

  while kill -0 "$pid" 2>/dev/null; do
    printf "\r  ⏳ %s (%ds) " "$msg" "$s"
    sleep 1
    s=$((s + 1))
  done

  wait "$pid" || rc=$?

  if [ $rc -eq 0 ]; then
    printf "\r  ${GREEN}✔${NC} %s (%ds)          \n" "$msg" "$s"
  else
    printf "\r  ${RED}✘${NC} %s — failed after %ds\n" "$msg" "$s"
    echo ""
    tail -20 "$logfile" | sed 's/^/      /'
    echo ""
    hint "Fix the issue above and re-run: make setup"
    rm -f "$logfile"
    exit 1
  fi
  rm -f "$logfile"
}

# ── Error handler ────────────────────────────────────────────────────
cleanup() {
  local rc=$?
  [ $rc -eq 0 ] && return
  echo ""
  fail "Something went wrong."
  hint "This script is idempotent — just fix the issue and re-run: make setup"
}
trap cleanup EXIT
trap 'echo ""; echo "  Interrupted."; exit 130' INT

# ── Banner ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  🔄  gdrive-git-sync setup${NC}"
echo -e "  ${DIM}Automatically version-control Drive files in git${NC}"
echo ""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 1 — Prerequisites
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase "1/4" "Prerequisites"

MISSING=0
for cmd in gcloud terraform git zip; do
  if command -v "$cmd" &>/dev/null; then
    ok "$cmd"
  else
    fail "$cmd — not found"
    case "$cmd" in
      gcloud)    hint "https://cloud.google.com/sdk/docs/install" ;;
      terraform) hint "https://developer.hashicorp.com/terraform/install" ;;
      git)       hint "https://git-scm.com/downloads" ;;
      zip)       hint "brew install zip  (macOS)  or  apt install zip  (Linux)" ;;
    esac
    MISSING=1
  fi
done

[ "$MISSING" -eq 1 ] && { fail "Install the tools above, then re-run."; exit 1; }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 2 — Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase "2/4" "Configuration"

FIRST_RUN=false
GIT_TOKEN_VALUE=""

if [ -f "$ENV_FILE" ]; then
  ok "Using existing .env"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  hint "Project: $GCP_PROJECT  |  Repo: $GIT_REPO_URL"
  hint "To change settings, edit .env and re-run."
else
  FIRST_RUN=true
  echo ""

  # ── GCP project ID ──
  while true; do
    read -rp "  GCP project ID: " GCP_PROJECT
    if [[ "$GCP_PROJECT" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
      break
    fi
    fail "Must be 6-30 chars: lowercase letters, digits, hyphens (e.g. my-cool-project)"
  done

  # ── Drive folder ID ──
  while true; do
    read -rp "  Drive folder ID or URL: " FOLDER_INPUT
    # Extract ID from URL if user pasted the whole thing
    if [[ "$FOLDER_INPUT" =~ /folders/([a-zA-Z0-9_-]+) ]]; then
      DRIVE_FOLDER_ID="${BASH_REMATCH[1]}"
      hint "Extracted folder ID: $DRIVE_FOLDER_ID"
      break
    elif [[ "$FOLDER_INPUT" =~ ^[a-zA-Z0-9_-]{10,}$ ]]; then
      DRIVE_FOLDER_ID="$FOLDER_INPUT"
      break
    fi
    fail "Paste the folder ID or the full Drive folder URL"
  done

  # ── Git repo URL ──
  while true; do
    read -rp "  Git repo URL (HTTPS): " GIT_REPO_URL
    if [[ "$GIT_REPO_URL" =~ ^https:// ]]; then
      break
    fi
    fail "Must start with https:// (e.g. https://github.com/you/repo.git)"
  done

  # ── Git branch ──
  read -rp "  Git branch [main]: " GIT_BRANCH
  GIT_BRANCH="${GIT_BRANCH:-main}"

  # ── Secret name ──
  read -rp "  Secret Manager name for git token [git-token]: " GIT_TOKEN_SECRET
  GIT_TOKEN_SECRET="${GIT_TOKEN_SECRET:-git-token}"

  # ── Git token ──
  echo ""
  info "Last thing — a personal access token so the bot can push commits."
  echo ""
  hint "GitHub → Settings → Developer Settings → Fine-grained tokens"
  hint "  Permission needed: Contents (read/write) on the target repo"
  echo ""
  hint "GitLab → Settings → Access Tokens"
  hint "  Scope needed: write_repository"
  echo ""
  while true; do
    read -rsp "  Paste your token (input is hidden): " GIT_TOKEN_VALUE
    echo ""
    if [ -n "$GIT_TOKEN_VALUE" ]; then break; fi
    fail "Token can't be empty"
  done

  # ── Write .env ──
  cat > "$ENV_FILE" <<EOF
# === Required ===
GCP_PROJECT=${GCP_PROJECT}
DRIVE_FOLDER_ID=${DRIVE_FOLDER_ID}
GIT_REPO_URL=${GIT_REPO_URL}
GIT_BRANCH=${GIT_BRANCH}
GIT_TOKEN_SECRET=${GIT_TOKEN_SECRET}

# === Optional (uncomment to override defaults) ===
# EXCLUDE_PATHS=Drafts/*,Archive/*
# SKIP_EXTENSIONS=.zip,.exe,.dmg,.iso
# MAX_FILE_SIZE_MB=100
# COMMIT_AUTHOR_NAME=Drive Sync Bot
# COMMIT_AUTHOR_EMAIL=sync@example.com
# FIRESTORE_COLLECTION=drive_sync_state
# DOCS_SUBDIR=docs
# GOOGLE_VERIFICATION_TOKEN=
EOF

  set -a; source "$ENV_FILE"; set +a
  echo ""
  ok ".env saved"
fi

# Validate essentials are present
: "${GCP_PROJECT:?GCP_PROJECT is required in .env}"
: "${DRIVE_FOLDER_ID:?DRIVE_FOLDER_ID is required in .env}"
: "${GIT_REPO_URL:?GIT_REPO_URL is required in .env}"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 3 — GCP setup + deploy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase "3/4" "Setting up GCP"

# ── Auth ──
CURRENT_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)
if [ -n "$CURRENT_ACCOUNT" ]; then
  ok "Authenticated as $CURRENT_ACCOUNT"
  ADC_FILE="${CLOUDSDK_CONFIG_DIR:-$HOME/.config/gcloud}/application_default_credentials.json"
  if [ ! -f "$ADC_FILE" ]; then
    info "Terraform needs application-default credentials..."
    gcloud auth application-default login
    ok "Application-default credentials saved"
  fi
else
  info "Opening browser to log in to GCP..."
  gcloud auth login
  ok "Logged in"
  info "One more — Terraform needs its own credentials..."
  gcloud auth application-default login
  ok "Application-default credentials saved"
fi

gcloud config set project "$GCP_PROJECT" 2>/dev/null
ok "Active project: $GCP_PROJECT"

# ── APIs ──
spin "Enabling GCP APIs" \
  gcloud services enable \
    cloudfunctions.googleapis.com \
    cloudscheduler.googleapis.com \
    firestore.googleapis.com \
    secretmanager.googleapis.com \
    drive.googleapis.com \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    artifactregistry.googleapis.com

# ── Source bucket ──
BUCKET="${GCP_PROJECT}-functions-source"
if gcloud storage buckets describe "gs://$BUCKET" &>/dev/null 2>&1; then
  ok "Source bucket: gs://$BUCKET"
else
  spin "Creating source bucket" \
    gcloud storage buckets create "gs://$BUCKET" --location=us-central1
fi

# ── Deploy ──
spin "Deploying infrastructure (this one takes a while)" \
  "$SCRIPT_DIR/deploy.sh"

# ── Git token ──
SECRET_NAME="${GIT_TOKEN_SECRET:-git-token}"
if [ -n "$GIT_TOKEN_VALUE" ]; then
  # First run — store the token we collected earlier
  TMPTOKEN=$(mktemp)
  printf '%s' "$GIT_TOKEN_VALUE" > "$TMPTOKEN"
  spin "Storing git token in Secret Manager" \
    gcloud secrets versions add "$SECRET_NAME" --data-file="$TMPTOKEN"
  rm -f "$TMPTOKEN"
  GIT_TOKEN_VALUE="" # clear from memory
elif gcloud secrets versions list "$SECRET_NAME" --limit=1 --format="value(name)" 2>/dev/null | grep -q .; then
  ok "Git token already in Secret Manager"
else
  echo ""
  info "No git token found in Secret Manager."
  hint "GitHub → Settings → Developer Settings → Fine-grained tokens"
  hint "GitLab → Settings → Access Tokens"
  echo ""
  while true; do
    read -rsp "  Paste your token (hidden): " GIT_TOKEN_VALUE
    echo ""
    if [ -n "$GIT_TOKEN_VALUE" ]; then break; fi
    fail "Token can't be empty"
  done
  TMPTOKEN=$(mktemp)
  printf '%s' "$GIT_TOKEN_VALUE" > "$TMPTOKEN"
  spin "Storing git token" \
    gcloud secrets versions add "$SECRET_NAME" --data-file="$TMPTOKEN"
  rm -f "$TMPTOKEN"
  GIT_TOKEN_VALUE=""
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 4 — What's left
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase "4/4" "Almost there — three manual steps remain"

SYNC_URL=$(terraform -chdir="$ROOT_DIR/infra" output -raw sync_handler_url 2>/dev/null || echo "<run make deploy first>")
SETUP_URL=$(terraform -chdir="$ROOT_DIR/infra" output -raw setup_watch_url 2>/dev/null || echo "<run make deploy first>")
SA_EMAIL=$(terraform -chdir="$ROOT_DIR/infra" output -raw service_account_email 2>/dev/null || echo "<run make deploy first>")

echo ""
printf "  ${BOLD}1. Verify your domain${NC} ${DIM}(one-time, required for Drive webhooks)${NC}\n"
hint "a) Google Search Console → Add Property → URL Prefix"
hint "   $SYNC_URL"
hint "b) Choose 'HTML file' verification — the function serves it automatically"
hint "c) API Console → Domain Verification → Add Domain"
hint "   https://console.cloud.google.com/apis/credentials/domainverification"

echo ""
printf "  ${BOLD}2. Share the Drive folder${NC}\n"
hint "Open your Drive folder → Share → add with Editor access:"
hint "$SA_EMAIL"

echo ""
printf "  ${BOLD}3. Start watching for changes${NC}\n"
hint "curl -X POST \"${SETUP_URL}?initial_sync=true\" \\"
hint "  -H \"Authorization: bearer \$(gcloud auth print-identity-token)\""

echo ""
echo -e "  ${GREEN}${BOLD}🎉  Setup complete!${NC}"
echo -e "  ${DIM}After the three steps above, files will sync automatically.${NC}"
echo ""

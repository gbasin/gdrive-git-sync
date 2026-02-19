#!/usr/bin/env bash
set -euo pipefail

# Interactive setup for gdrive-git-sync
# Idempotent — safe to re-run at any point.
#
# Usage:
#   ./scripts/setup.sh                  # Interactive (guided prompts)
#   ./scripts/setup.sh --non-interactive # Agent/CI mode (no prompts, uses .env)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT_DIR/.env"
TFVARS_FILE="$ROOT_DIR/infra/terraform.tfvars"

# ── Flags ────────────────────────────────────────────────────────────
AUTO=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --non-interactive|--auto|--ci) AUTO=true; shift ;;
    -h|--help)
      echo "Usage: $0 [--non-interactive]"
      echo ""
      echo "  --non-interactive  Agent/CI mode: no prompts, auto-installs prereqs,"
      echo "                     requires .env to exist, accepts GIT_TOKEN_VALUE env var."
      exit 0
      ;;
    *) echo "Unknown flag: $1 (try --help)"; exit 1 ;;
  esac
done

# ── Colors & helpers ─────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

ok()   { printf "  ${GREEN}✔${NC} %s\n" "$*"; }
fail() { printf "  ${RED}✘${NC} %s\n" "$*"; }
info() { printf "  ${BLUE}▸${NC} %s\n" "$*"; }
warn() { printf "  ${YELLOW}!${NC} %s\n" "$*"; }
hint() { printf "    ${DIM}%s${NC}\n" "$*"; }

phase() {
  echo ""
  printf "${BOLD}[$1] $2${NC}\n"
}

# Prompt helper — in auto mode, uses default; in interactive mode, asks.
ask() {
  local prompt="$1" default="${2:-}" var_name="$3"
  if $AUTO; then
    printf -v "$var_name" '%s' "$default"
    return
  fi
  local input
  read -rp "  $prompt" input
  printf -v "$var_name" '%s' "${input:-$default}"
}

# Spinner — runs a command in the background, shows elapsed time.
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
$AUTO && echo -e "  ${DIM}Running in non-interactive mode${NC}"
echo ""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 1 — Prerequisites
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase "1/4" "Prerequisites"

# Detect package manager
PKG_MGR=""
if command -v brew &>/dev/null; then
  PKG_MGR="brew"
elif command -v apt-get &>/dev/null; then
  PKG_MGR="apt"
fi

# Map tools → install commands
brew_pkg() {
  case "$1" in
    gcloud)    echo "--cask google-cloud-sdk" ;;
    terraform) echo "hashicorp/tap/terraform" ;;
    git)       echo "git" ;;
    zip)       echo "zip" ;;
  esac
}

apt_pkg() {
  case "$1" in
    gcloud)    echo "" ;;  # no simple apt package
    terraform) echo "" ;;  # no simple apt package
    git)       echo "git" ;;
    zip)       echo "zip" ;;
  esac
}

install_url() {
  case "$1" in
    gcloud)    echo "https://cloud.google.com/sdk/docs/install" ;;
    terraform) echo "https://developer.hashicorp.com/terraform/install" ;;
    git)       echo "https://git-scm.com/downloads" ;;
    zip)       echo "your system package manager" ;;
  esac
}

# Check what's missing
MISSING_TOOLS=()
for cmd in gcloud terraform git zip; do
  if command -v "$cmd" &>/dev/null; then
    ok "$cmd"
  else
    fail "$cmd — not found"
    MISSING_TOOLS+=("$cmd")
  fi
done

# Install missing tools
if [ ${#MISSING_TOOLS[@]} -gt 0 ]; then
  echo ""

  # Determine what we can auto-install
  CAN_INSTALL=()
  MANUAL_INSTALL=()
  for cmd in "${MISSING_TOOLS[@]}"; do
    if [ "$PKG_MGR" = "brew" ]; then
      CAN_INSTALL+=("$cmd")
    elif [ "$PKG_MGR" = "apt" ] && [ -n "$(apt_pkg "$cmd")" ]; then
      CAN_INSTALL+=("$cmd")
    else
      MANUAL_INSTALL+=("$cmd")
    fi
  done

  # Install what we can
  if [ ${#CAN_INSTALL[@]} -gt 0 ]; then
    INSTALL_LIST=$(printf ", %s" "${CAN_INSTALL[@]}"); INSTALL_LIST=${INSTALL_LIST:2}

    DO_INSTALL=false
    if $AUTO; then
      info "Auto-installing: $INSTALL_LIST"
      DO_INSTALL=true
    else
      read -rp "  Install $INSTALL_LIST with $PKG_MGR? [Y/n]: " ANSWER
      [[ ! "${ANSWER:-Y}" =~ ^[Nn] ]] && DO_INSTALL=true
    fi

    if $DO_INSTALL; then
      # Tap hashicorp if we need terraform via brew
      if [ "$PKG_MGR" = "brew" ]; then
        for cmd in "${CAN_INSTALL[@]}"; do
          if [ "$cmd" = "terraform" ]; then
            spin "Adding hashicorp/tap to brew" brew tap hashicorp/tap
            break
          fi
        done
      fi

      for cmd in "${CAN_INSTALL[@]}"; do
        if [ "$PKG_MGR" = "brew" ]; then
          spin "Installing $cmd" brew install $(brew_pkg "$cmd")
        elif [ "$PKG_MGR" = "apt" ]; then
          spin "Installing $cmd" sudo apt-get install -y $(apt_pkg "$cmd")
        fi
      done

      # Verify installations
      for cmd in "${CAN_INSTALL[@]}"; do
        if command -v "$cmd" &>/dev/null; then
          ok "$cmd installed"
        else
          # gcloud via brew cask may need PATH sourcing
          if [ "$cmd" = "gcloud" ] && [ "$PKG_MGR" = "brew" ]; then
            GCLOUD_PATH="$(brew --prefix)/share/google-cloud-sdk"
            if [ -f "$GCLOUD_PATH/path.bash.inc" ]; then
              # shellcheck disable=SC1091
              source "$GCLOUD_PATH/path.bash.inc"
            fi
            if command -v gcloud &>/dev/null; then
              ok "$cmd installed (sourced PATH from brew)"
            else
              fail "$cmd installed but not in PATH — restart your terminal and re-run"
              exit 1
            fi
          else
            fail "$cmd install succeeded but command not found — restart your terminal and re-run"
            exit 1
          fi
        fi
      done
    else
      for cmd in "${CAN_INSTALL[@]}"; do
        hint "Install manually: $(install_url "$cmd")"
      done
      fail "Install missing tools, then re-run."
      exit 1
    fi
  fi

  # Report anything we couldn't auto-install
  if [ ${#MANUAL_INSTALL[@]} -gt 0 ]; then
    echo ""
    fail "These tools can't be auto-installed on this system:"
    for cmd in "${MANUAL_INSTALL[@]}"; do
      hint "$cmd → $(install_url "$cmd")"
    done
    fail "Install them manually, then re-run."
    exit 1
  fi
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 2 — Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase "2/4" "Configuration"

FIRST_RUN=false
GIT_TOKEN_VALUE="${GIT_TOKEN_VALUE:-}"  # accept from env for agent mode

if [ -f "$ENV_FILE" ]; then
  ok "Using existing .env"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  hint "Project: $GCP_PROJECT  |  Repo: $GIT_REPO_URL"
  hint "To change settings, edit .env and re-run."
elif $AUTO; then
  fail ".env not found. In non-interactive mode, .env must exist."
  hint "Create it from the example:  cp .env.example .env"
  hint "Then fill in the values and re-run."
  exit 1
else
  FIRST_RUN=true
  info "We need four things. I'll walk you through each one."

  # ── 1. GCP project ──
  echo ""
  printf "  ${BOLD}A) Google Cloud project${NC}\n"
  hint "This is where your Cloud Functions, database, and secrets will live."
  hint "If you don't have a project yet:"
  hint "  1. Go to https://console.cloud.google.com"
  hint "  2. Click the project dropdown at the top → \"New Project\""
  hint "  3. Give it any name (e.g. \"drive-sync\") and create it"
  hint "  4. Make sure billing is enabled (required for Cloud Functions)"
  hint "The project ID is the lowercase string shown under the name"
  hint "(e.g. \"drive-sync-429301\" — NOT the display name)."
  echo ""
  while true; do
    read -rp "  Project ID: " GCP_PROJECT
    if [[ "$GCP_PROJECT" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
      break
    fi
    fail "Doesn't look right — project IDs are lowercase letters, digits, and hyphens (6-30 chars)"
    hint "Example: my-project-123456"
  done

  # ── 2. Drive folder ──
  echo ""
  printf "  ${BOLD}B) Google Drive folder${NC}\n"
  hint "Which Drive folder should we watch for changes?"
  hint "  1. Open the folder in Google Drive"
  hint "  2. Look at the URL bar — it looks like:"
  hint "     drive.google.com/drive/folders/1aBcD_eFgHiJkLmNoPqRsTuVwXyZ"
  hint "You can paste the whole URL or just the ID part after /folders/."
  echo ""
  while true; do
    read -rp "  Folder ID or URL: " FOLDER_INPUT
    if [[ "$FOLDER_INPUT" =~ /folders/([a-zA-Z0-9_-]+) ]]; then
      DRIVE_FOLDER_ID="${BASH_REMATCH[1]}"
      ok "Got it — extracted ID: ${DRIVE_FOLDER_ID:0:20}..."
      break
    elif [[ "$FOLDER_INPUT" =~ ^[a-zA-Z0-9_-]{10,}$ ]]; then
      DRIVE_FOLDER_ID="$FOLDER_INPUT"
      break
    fi
    fail "That doesn't look like a folder ID or Drive URL"
    hint "Open your folder in Drive and copy the URL from the browser address bar"
  done

  # ── 3. Git repo ──
  echo ""
  printf "  ${BOLD}C) Git repository${NC}\n"
  hint "Where should the synced files be pushed? You need a repo on"
  hint "GitHub, GitLab, Bitbucket, or any host that supports HTTPS push."
  hint "If you don't have one yet:"
  hint "  GitHub: github.com/new → create a repo (can be private)"
  hint "  GitLab: gitlab.com/projects/new"
  hint "Copy the HTTPS clone URL (e.g. https://github.com/you/my-docs.git)."
  echo ""
  while true; do
    read -rp "  Repo URL: " GIT_REPO_URL
    if [[ "$GIT_REPO_URL" =~ ^https:// ]]; then
      break
    fi
    fail "Needs to be an HTTPS URL (starts with https://)"
    hint "Example: https://github.com/yourname/your-repo.git"
  done

  read -rp "  Branch to push to [main]: " GIT_BRANCH
  GIT_BRANCH="${GIT_BRANCH:-main}"

  # ── 4. Git token ──
  echo ""
  printf "  ${BOLD}D) Git personal access token${NC}\n"
  hint "The bot needs a token to push commits to your repo."
  hint "This is stored securely in Google Cloud Secret Manager — never in code."
  echo ""

  # Detect host to give specific instructions
  if [[ "$GIT_REPO_URL" =~ github\.com ]]; then
    hint "Since you're using GitHub:"
    hint "  1. Go to https://github.com/settings/tokens?type=beta"
    hint "     (Settings → Developer Settings → Fine-grained tokens)"
    hint "  2. Click \"Generate new token\""
    hint "  3. Under \"Repository access\" → select \"Only select repositories\""
    hint "     and pick your repo"
    hint "  4. Under \"Permissions\" → \"Repository permissions\" →"
    hint "     set \"Contents\" to \"Read and write\""
    hint "  5. Click \"Generate token\" and copy it"
  elif [[ "$GIT_REPO_URL" =~ gitlab\.com ]]; then
    hint "Since you're using GitLab:"
    hint "  1. Go to your repo → Settings → Access Tokens"
    hint "  2. Create a project access token"
    hint "  3. Scope needed: write_repository"
    hint "  4. Copy the token"
  elif [[ "$GIT_REPO_URL" =~ bitbucket\.org ]]; then
    hint "Since you're using Bitbucket:"
    hint "  1. Go to your repo → Repository settings → Access tokens"
    hint "  2. Create a token with \"Repositories: Write\" permission"
    hint "  3. Copy the token"
  else
    hint "Create a personal access token with push (write) access to your repo."
    hint "Check your git host's docs for how to generate one."
  fi

  echo ""
  while true; do
    read -rsp "  Paste your token (input is hidden): " GIT_TOKEN_VALUE
    echo ""
    if [ -n "$GIT_TOKEN_VALUE" ]; then break; fi
    fail "Token can't be empty"
  done

  GIT_TOKEN_SECRET="git-token"

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
ADC_FILE="${CLOUDSDK_CONFIG_DIR:-$HOME/.config/gcloud}/application_default_credentials.json"

if $AUTO; then
  # Non-interactive: verify auth exists, don't try to open a browser
  if [ -n "$CURRENT_ACCOUNT" ]; then
    ok "Authenticated as $CURRENT_ACCOUNT"
  elif [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]; then
    ok "Using service account from GOOGLE_APPLICATION_CREDENTIALS"
  else
    fail "No GCP authentication found."
    hint "Before running in non-interactive mode, authenticate with one of:"
    hint "  gcloud auth login && gcloud auth application-default login"
    hint "  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json"
    exit 1
  fi
  if [ ! -f "$ADC_FILE" ] && [ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]; then
    fail "Terraform needs application-default credentials."
    hint "Run: gcloud auth application-default login"
    exit 1
  fi
else
  # Interactive: guide through login if needed
  if [ -n "$CURRENT_ACCOUNT" ]; then
    ok "Authenticated as $CURRENT_ACCOUNT"
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

store_token() {
  local token_val="$1"
  local tmpfile; tmpfile=$(mktemp)
  printf '%s' "$token_val" > "$tmpfile"
  spin "Storing git token in Secret Manager" \
    gcloud secrets versions add "$SECRET_NAME" --data-file="$tmpfile"
  rm -f "$tmpfile"
}

HAS_VERSION=$(gcloud secrets versions list "$SECRET_NAME" --limit=1 --format="value(name)" 2>/dev/null || true)

if [ -n "$GIT_TOKEN_VALUE" ]; then
  # Token provided via env var (agent mode) or collected during Phase 2
  store_token "$GIT_TOKEN_VALUE"
  GIT_TOKEN_VALUE="" # clear from memory
elif [ -n "$HAS_VERSION" ]; then
  ok "Git token already in Secret Manager"
elif $AUTO; then
  warn "No git token found in Secret Manager and none provided."
  hint "Set GIT_TOKEN_VALUE env var and re-run, or add manually:"
  hint "  echo -n 'YOUR_TOKEN' | gcloud secrets versions add $SECRET_NAME --data-file=-"
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
  store_token "$GIT_TOKEN_VALUE"
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

# ── Machine-readable summary for agent mode ──
if $AUTO; then
  echo "--- AGENT SUMMARY ---"
  echo "STATUS: success"
  echo "MANUAL_STEPS_REQUIRED: 3"
  echo ""
  echo "STEP_1: Domain verification"
  echo "  ACTION: Register webhook URL with Google Search Console and API Console Domain Verification"
  echo "  SYNC_HANDLER_URL: $SYNC_URL"
  echo "  SEARCH_CONSOLE: https://search.google.com/search-console"
  echo "  DOMAIN_VERIFICATION: https://console.cloud.google.com/apis/credentials/domainverification"
  echo "  VERIFICATION_METHOD: HTML file (auto-served by the function)"
  echo ""
  echo "STEP_2: Share Drive folder"
  echo "  ACTION: Share the monitored Drive folder with the service account"
  echo "  SERVICE_ACCOUNT: $SA_EMAIL"
  echo "  ACCESS_LEVEL: Editor"
  echo ""
  echo "STEP_3: Initialize watch channel"
  echo "  ACTION: Send POST request to start watching for Drive changes"
  echo "  COMMAND: curl -X POST \"${SETUP_URL}?initial_sync=true\" -H \"Authorization: bearer \$(gcloud auth print-identity-token)\""
  echo ""
  if [ -z "$HAS_VERSION" ] && [ -z "${GIT_TOKEN_VALUE:-}" ]; then
    echo "WARNING: Git token not stored in Secret Manager."
    echo "  ACTION: Store token before initializing watch"
    echo "  COMMAND: echo -n 'YOUR_TOKEN' | gcloud secrets versions add $SECRET_NAME --data-file=-"
    echo ""
  fi
  echo "--- END AGENT SUMMARY ---"
fi

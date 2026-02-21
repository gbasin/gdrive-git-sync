#!/usr/bin/env bash
# shellcheck disable=SC2059  # color variables in printf format strings are intentional throughout
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

# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"

# ── Flags ────────────────────────────────────────────────────────────
AUTO=false
DRY_RUN=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --non-interactive|--auto|--ci) AUTO=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help)
      echo "Usage: $0 [--non-interactive] [--dry-run]"
      echo ""
      echo "  --non-interactive  Agent/CI mode: no prompts, auto-installs prereqs,"
      echo "                     requires .env to exist, accepts GIT_TOKEN_VALUE env var."
      echo "  --dry-run          Walk through the entire flow without executing anything."
      echo "                     Uses placeholder values if .env doesn't exist."
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

# Run a command, or return simulated output in dry-run mode.
# Usage: result=$(sim "simulated output" command arg1 arg2)
sim() {
  local sim_output="$1"; shift
  if $DRY_RUN; then
    echo "$sim_output"
    return 0
  fi
  "$@"
}

# Spinner — runs a command in the background, shows elapsed time.
# In dry-run mode, prints what would happen without executing.
spin() {
  local msg="$1"; shift
  if $DRY_RUN; then
    printf "  ${GREEN}✔${NC} %s ${DIM}[dry-run: %s]${NC}\n" "$msg" "$*"
    return 0
  fi

  local logfile rc s pid reply

  while true; do
    logfile=$(mktemp)
    rc=0; s=0

    "$@" >"$logfile" 2>&1 &
    pid=$!

    while kill -0 "$pid" 2>/dev/null; do
      printf "\r  ⏳ %s (%ds) " "$msg" "$s"
      sleep 1
      s=$((s + 1))
    done

    wait "$pid" || rc=$?

    if [ $rc -eq 0 ]; then
      printf "\r  ${GREEN}✔${NC} %s (%ds)          \n" "$msg" "$s"
      rm -f "$logfile"
      return 0
    fi

    printf "\r  ${RED}✘${NC} %s — failed after %ds\n" "$msg" "$s"
    echo ""
    tail -20 "$logfile" | sed 's/^/      /'
    echo ""
    rm -f "$logfile"
    if $AUTO; then
      hint "Fix the issue above and re-run: make setup"
      ERROR_HANDLED=true; exit 1
    fi
    hint "Tip: paste the error above into Claude or ChatGPT for help."
    read -rp "  Retry? [Y/n]: " reply
    if [[ "${reply:-Y}" =~ ^[Nn] ]]; then
      ERROR_HANDLED=true; exit 1
    fi
  done
}

# ── Error handler ────────────────────────────────────────────────────
ERROR_HANDLED=false  # set to true when a specific error message was already shown
cleanup() {
  local rc=$?
  [ $rc -eq 0 ] && return
  $ERROR_HANDLED && return
  echo ""
  fail "Something went wrong."
  hint "Your progress is saved in .env — you can safely re-run: make setup"
  hint ""
  hint "Stuck? Paste this into Claude, ChatGPT, or any AI assistant:"
  hint "  I'm setting up gdrive-git-sync (https://github.com/garybasin/gdrive-git-sync)."
  hint "  It failed during setup. Here's the error output:"
  hint "  <paste everything above>"
}
trap cleanup EXIT
trap 'echo ""; echo "  Interrupted."; ERROR_HANDLED=true; exit 130' INT

# ── Banner ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  🔄  gdrive-git-sync setup${NC}"
echo -e "  ${DIM}Automatically version-control Drive files in git${NC}"
$AUTO && echo -e "  ${DIM}Running in non-interactive mode${NC}"
$DRY_RUN && echo -e "  ${YELLOW}${BOLD}DRY RUN${NC} ${DIM}— nothing will be created or modified${NC}"
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

# ── Required tools ──
MISSING_TOOLS=()
for cmd in gcloud terraform git zip; do
  if command -v "$cmd" &>/dev/null; then
    ok "$cmd"
  elif $DRY_RUN; then
    warn "$cmd — not found [dry-run: skipping]"
  else
    fail "$cmd — not found"
    MISSING_TOOLS+=("$cmd")
  fi
done

# Install missing required tools
if [ ${#MISSING_TOOLS[@]} -gt 0 ]; then
  echo ""

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
          # shellcheck disable=SC2046  # word splitting intentional: brew_pkg may return "--cask pkg"
          spin "Installing $cmd" brew install $(brew_pkg "$cmd")
        elif [ "$PKG_MGR" = "apt" ]; then
          spin "Installing $cmd" sudo apt-get install -y "$(apt_pkg "$cmd")"
        fi
      done

      for cmd in "${CAN_INSTALL[@]}"; do
        if command -v "$cmd" &>/dev/null; then
          ok "$cmd installed"
        elif [ "$cmd" = "gcloud" ] && [ "$PKG_MGR" = "brew" ]; then
          GCLOUD_PATH="$(brew --prefix)/share/google-cloud-sdk"
          if [ -f "$GCLOUD_PATH/path.bash.inc" ]; then
            # shellcheck disable=SC1091
            source "$GCLOUD_PATH/path.bash.inc"
          fi
          if command -v gcloud &>/dev/null; then
            ok "$cmd installed (sourced PATH from brew)"
          else
            fail "$cmd installed but not in PATH — restart your terminal and re-run"
            ERROR_HANDLED=true; exit 1
          fi
        else
          fail "$cmd install succeeded but command not found — restart your terminal and re-run"
          ERROR_HANDLED=true; exit 1
        fi
      done
    else
      for cmd in "${CAN_INSTALL[@]}"; do
        hint "Install manually: $(install_url "$cmd")"
      done
      fail "Install missing tools, then re-run."
      ERROR_HANDLED=true; exit 1
    fi
  fi

  if [ ${#MANUAL_INSTALL[@]} -gt 0 ]; then
    echo ""
    fail "These tools can't be auto-installed on this system:"
    for cmd in "${MANUAL_INSTALL[@]}"; do
      hint "$cmd → $(install_url "$cmd")"
    done
    if $AUTO; then
      fail "Install them manually, then re-run."
      ERROR_HANDLED=true; exit 1
    fi
    echo ""
    read -rp "  Press Enter after installing them (or Ctrl-C to quit)..."
    # Re-check
    ALL_FOUND=true
    for cmd in "${MANUAL_INSTALL[@]}"; do
      if command -v "$cmd" &>/dev/null; then
        ok "$cmd found"
      else
        fail "$cmd still not found — make sure it's in your PATH"
        ALL_FOUND=false
      fi
    done
    if ! $ALL_FOUND; then
      hint "You may need to restart your terminal for PATH changes to take effect."
      ERROR_HANDLED=true; exit 1
    fi
  fi
fi

# ── Optional: GitHub CLI for automatic repo creation ──
if ! command -v gh &>/dev/null; then
  if [ "$PKG_MGR" = "brew" ]; then
    echo ""
    DO_GH=false
    if $AUTO; then
      info "Installing gh (GitHub CLI) for automatic repo creation"
      DO_GH=true
    else
      read -rp "  Install GitHub CLI (gh) for automatic repo creation? [Y/n]: " GH_ANSWER
      [[ ! "${GH_ANSWER:-Y}" =~ ^[Nn] ]] && DO_GH=true
    fi
    if $DO_GH; then
      spin "Installing gh" brew install gh
      ok "gh installed"
    fi
  fi
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 2 — Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase "2/4" "Configuration"

FULL_AUTH_DONE=false
GIT_TOKEN_VALUE="${GIT_TOKEN_VALUE:-}"  # accept from env for agent mode
ENV_COMPLETE=false

# Load existing .env if present
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a

  # Treat "root" as missing — My Drive root can't be shared with the bot
  if [ "${DRIVE_FOLDER_ID:-}" = "root" ]; then
    warn "DRIVE_FOLDER_ID=\"root\" (My Drive) can't be shared with the bot — you'll pick a specific folder."
    DRIVE_FOLDER_ID=""
  fi

  # Check if .env is complete or partial (from a previous crash)
  if [ -n "${GCP_PROJECT:-}" ] && [ -n "${DRIVE_FOLDER_ID:-}" ] && [ -n "${GIT_REPO_URL:-}" ]; then
    ok "Using existing .env (your saved configuration)"
    hint "Project: $GCP_PROJECT  |  Repo: $GIT_REPO_URL"
    hint "To change settings, edit .env in a text editor and re-run."
    ENV_COMPLETE=true
  else
    info "Found partial .env from a previous run — picking up where we left off."
    [ -n "${GCP_PROJECT:-}" ] && ok "GCP project: $GCP_PROJECT"
    [ -n "${DRIVE_FOLDER_ID:-}" ] && ok "Drive folder: ${DRIVE_FOLDER_ID:0:20}..."
    [ -n "${GIT_REPO_URL:-}" ] && ok "Git repo: $GIT_REPO_URL"
  fi
fi

if $ENV_COMPLETE; then
  : # nothing to do — .env is complete
elif $DRY_RUN; then
  # Fill in placeholders for any missing values so dry-run never prompts
  GCP_PROJECT="${GCP_PROJECT:-my-project-12345}"
  DRIVE_FOLDER_ID="${DRIVE_FOLDER_ID:-1aBcDeFgHiJkLmNoPqRsTuVwXyZ}"
  GIT_REPO_URL="${GIT_REPO_URL:-https://github.com/yourname/drive-sync.git}"
  GIT_BRANCH="${GIT_BRANCH:-main}"
  GIT_TOKEN_SECRET="${GIT_TOKEN_SECRET:-git-token}"
  GIT_TOKEN_VALUE="${GIT_TOKEN_VALUE:-ghp_xxxxxxxxxxxxxxxxxxxx}"
  ok "Using placeholder values for missing fields [dry-run]"
  hint "Project: $GCP_PROJECT  |  Repo: $GIT_REPO_URL"
elif $AUTO; then
  if [ -f "$ENV_FILE" ]; then
    fail ".env exists but is missing required values."
    [ -z "${GCP_PROJECT:-}" ]    && hint "  Missing: GCP_PROJECT"
    [ -z "${DRIVE_FOLDER_ID:-}" ] && hint "  Missing: DRIVE_FOLDER_ID"
    [ -z "${GIT_REPO_URL:-}" ]   && hint "  Missing: GIT_REPO_URL"
  else
    fail ".env not found."
    hint "Create it from the example:  cp .env.example .env"
  fi
  hint "Fill in the required values and re-run."
  ERROR_HANDLED=true; exit 1
else
  # ── Check for previous partial run ──────────────────────────────────
  # If gcloud has a project set from a previous run that crashed before
  # .env was written, recover it instead of starting over.
  PREV_PROJECT=$(sim "" gcloud config get-value project 2>/dev/null || true)
  if [ -n "$PREV_PROJECT" ] && [[ "$PREV_PROJECT" =~ ^[a-z] ]] && [[ "$PREV_PROJECT" != "NONE" ]]; then
    echo ""
    info "Found GCP project from a previous run: $PREV_PROJECT"
    read -rp "  Continue with this project? [Y/n]: " USE_PREV
    if [[ ! "${USE_PREV:-Y}" =~ ^[Nn] ]]; then
      GCP_PROJECT="$PREV_PROJECT"
      ok "Using project $GCP_PROJECT"
      # Check auth state — verify token is actually valid, not just configured
      CURRENT_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)
      if [ -n "$CURRENT_ACCOUNT" ] && gcloud auth print-access-token &>/dev/null; then
        ok "Authenticated as $CURRENT_ACCOUNT"
        FULL_AUTH_DONE=true
      fi
    else
      PREV_PROJECT=""
    fi
  fi

  # Helper: write .env with whatever we have so far, so progress survives crashes.
  write_env() {
    cat > "$ENV_FILE" <<ENVEOF
# === Required ===
GCP_PROJECT="${GCP_PROJECT:-}"
DRIVE_FOLDER_ID="${DRIVE_FOLDER_ID:-}"
GIT_REPO_URL="${GIT_REPO_URL:-}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_TOKEN_SECRET="${GIT_TOKEN_SECRET:-git-token}"

# === Optional (uncomment to override defaults) ===
# EXCLUDE_PATHS="Drafts/*,Archive/*"
# SKIP_EXTENSIONS=".zip,.exe,.dmg,.iso"
# MAX_FILE_SIZE_MB=100
# COMMIT_AUTHOR_NAME="Drive Sync Bot"
# COMMIT_AUTHOR_EMAIL="sync@example.com"
# FIRESTORE_COLLECTION="drive_sync_state"
# DOCS_SUBDIR="docs"
# GOOGLE_VERIFICATION_TOKEN=""
ENVEOF
  }

  if [ -z "${GCP_PROJECT:-}" ]; then
    # ── Overview ────────────────────────────────────────────────────────
    info "I need to collect four things from you:"
    hint "  A) A Google Cloud project  (we can create one)"
    hint "  B) A Google Drive folder   (the one you want to sync)"
    hint "  C) A GitHub/GitLab repo    (we can create one)"
    hint "  D) A personal access token (a password for the bot)"
    echo ""
    hint "This takes about 10 minutes. At each prompt, press Enter to"
    hint "accept the suggested value shown in [brackets]."

    # ── A) GCP project ──────────────────────────────────────────────────
    echo ""
    printf "  ${BOLD}A) Google Cloud project${NC}\n"
    hint "Google Cloud is where the sync service will run. A \"project\" is"
    hint "just a container for it — like a folder for your cloud resources."
    echo ""
    read -rp "  Do you already have a Google Cloud project for this? [y/N]: " HAS_PROJECT

    if [[ "${HAS_PROJECT:-}" =~ ^[Yy] ]]; then
      hint "Find your project ID at console.cloud.google.com — it's the"
      hint "lowercase string in the URL (e.g. \"drive-sync-429301\")."
      echo ""
      while true; do
        read -rp "  Project ID: " GCP_PROJECT
        if validate_gcp_project_id "$GCP_PROJECT"; then
          break
        fi
        fail "That doesn't look like a project ID."
        hint "It should be lowercase letters, digits, and hyphens (e.g. my-project-123456)"
      done
    else
      hint "No problem — I'll create one for you."

      # ── Auth ──
      echo ""
      info "First, let's sign in to Google Cloud."
      hint "A browser window will open — sign in with whatever Google account"
      hint "you want to use. (If you're already signed in, it'll be quick.)"
      echo ""
      CURRENT_ACCOUNT=$(sim "dryrun@example.com" gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)
      if [ -n "$CURRENT_ACCOUNT" ]; then
        # Verify token is still valid, not just that an account is configured
        if $DRY_RUN || gcloud auth print-access-token &>/dev/null; then
          ok "Already signed in as $CURRENT_ACCOUNT"
        else
          hint "Session for $CURRENT_ACCOUNT has expired — let's refresh it."
          read -rp "  Press Enter to open the browser..."
          gcloud auth login
          ok "Signed in"
        fi
      else
        read -rp "  Press Enter to open the browser..."
        if ! $DRY_RUN; then gcloud auth login; fi
        ok "Signed in"
      fi
      ADC_FILE="${CLOUDSDK_CONFIG_DIR:-$HOME/.config/gcloud}/application_default_credentials.json"
      if [ ! -f "$ADC_FILE" ] && ! $DRY_RUN; then
        echo ""
        info "One more sign-in — same account, different permission."
        hint "This lets the setup tool create resources on your behalf."
        gcloud auth application-default login
        ok "Done"
      fi
      FULL_AUTH_DONE=true

      # ── Create project ──
      echo ""
      SUGGESTED_ID="gdrive-sync-$(( RANDOM % 90000 + 10000 ))"
      info "Now I'll create a Google Cloud project."
      hint "Every project needs a unique ID. I've suggested one below —"
      hint "just press Enter to use it, or type your own."
      echo ""
      read -rp "  Project ID [$SUGGESTED_ID]: " GCP_PROJECT
      GCP_PROJECT="${GCP_PROJECT:-$SUGGESTED_ID}"

      while ! validate_gcp_project_id "$GCP_PROJECT"; do
        fail "That doesn't look right — use lowercase letters, digits, and hyphens."
        read -rp "  Project ID [$SUGGESTED_ID]: " GCP_PROJECT
        GCP_PROJECT="${GCP_PROJECT:-$SUGGESTED_ID}"
      done

      # Idempotent — skip if project already exists (e.g. re-run after partial failure)
      PROJECT_EXISTS=false
      if $DRY_RUN; then
        PROJECT_EXISTS=false  # simulate creation path in dry-run
      elif gcloud projects describe "$GCP_PROJECT" &>/dev/null; then
        PROJECT_EXISTS=true
      fi

      if $PROJECT_EXISTS; then
        ok "Project $GCP_PROJECT already exists — reusing it"
      elif $DRY_RUN; then
        ok "Would create project $GCP_PROJECT [dry-run]"
      else
        # Run inline (not via spin) so we can catch specific errors like ToS
        CREATE_LOG=$(mktemp)
        CREATE_RC=0
        CREATE_S=0

        gcloud projects create "$GCP_PROJECT" --name="gdrive-git-sync" >"$CREATE_LOG" 2>&1 &
        CREATE_PID=$!

        while kill -0 "$CREATE_PID" 2>/dev/null; do
          printf "\r  ⏳ Creating project %s (%ds) " "$GCP_PROJECT" "$CREATE_S"
          sleep 1
          CREATE_S=$((CREATE_S + 1))
        done
        wait "$CREATE_PID" || CREATE_RC=$?

        if [ $CREATE_RC -eq 0 ]; then
          printf "\r  ${GREEN}✔${NC} Created project: %s (%ds)          \n" "$GCP_PROJECT" "$CREATE_S"
          rm -f "$CREATE_LOG"
        elif grep -q "Terms of Service" "$CREATE_LOG"; then
          printf "\r  ${RED}✘${NC} Couldn't create the project — Google needs you to accept their Terms of Service first.\n"
          echo ""
          hint "This is a one-time thing. I'll open the page for you —"
          hint "make sure you're signed in as $CURRENT_ACCOUNT in the browser,"
          hint "accept the Terms of Service, then come back and press Enter."
          echo ""
          open "https://console.cloud.google.com" 2>/dev/null \
            || xdg-open "https://console.cloud.google.com" 2>/dev/null \
            || hint "Open: https://console.cloud.google.com"
          read -rp "  Press Enter after accepting the Terms of Service..."
          echo ""
          info "Retrying project creation..."
          CREATE_LOG2=$(mktemp)
          CREATE_S=0
          gcloud projects create "$GCP_PROJECT" --name="gdrive-git-sync" >"$CREATE_LOG2" 2>&1 &
          CREATE_PID=$!
          while kill -0 "$CREATE_PID" 2>/dev/null; do
            printf "\r  ⏳ Creating project %s (%ds) " "$GCP_PROJECT" "$CREATE_S"
            sleep 1
            CREATE_S=$((CREATE_S + 1))
          done
          CREATE_RC=0
          wait "$CREATE_PID" || CREATE_RC=$?
          if [ $CREATE_RC -eq 0 ]; then
            printf "\r  ${GREEN}✔${NC} Created project: %s (%ds)          \n" "$GCP_PROJECT" "$CREATE_S"
          else
            printf "\r  ${RED}✘${NC} Still couldn't create the project.\n"
            hint "Make sure you accepted the Terms of Service at:"
            hint "  https://console.cloud.google.com"
            hint "Then re-run: make setup"
            rm -f "$CREATE_LOG" "$CREATE_LOG2"
            ERROR_HANDLED=true; exit 1
          fi
          rm -f "$CREATE_LOG2"
        elif grep -q "already in use" "$CREATE_LOG"; then
          printf "\r  ${RED}✘${NC} That project ID is already taken by someone else.\n"
          rm -f "$CREATE_LOG"
          while true; do
            SUGGESTED_ID="gdrive-sync-$(( RANDOM % 90000 + 10000 ))"
            read -rp "  Try a different ID [$SUGGESTED_ID]: " GCP_PROJECT
            GCP_PROJECT="${GCP_PROJECT:-$SUGGESTED_ID}"
            if ! validate_gcp_project_id "$GCP_PROJECT"; then
              fail "Use lowercase letters, digits, and hyphens (6-30 chars)."
              continue
            fi
            CREATE_LOG=$(mktemp)
            CREATE_S=0
            gcloud projects create "$GCP_PROJECT" --name="gdrive-git-sync" >"$CREATE_LOG" 2>&1 &
            CREATE_PID=$!
            while kill -0 "$CREATE_PID" 2>/dev/null; do
              printf "\r  ⏳ Creating project %s (%ds) " "$GCP_PROJECT" "$CREATE_S"
              sleep 1
              CREATE_S=$((CREATE_S + 1))
            done
            CREATE_RC=0
            wait "$CREATE_PID" || CREATE_RC=$?
            if [ $CREATE_RC -eq 0 ]; then
              printf "\r  ${GREEN}✔${NC} Created project: %s (%ds)          \n" "$GCP_PROJECT" "$CREATE_S"
              rm -f "$CREATE_LOG"
              break
            elif grep -q "already in use" "$CREATE_LOG"; then
              printf "\r  ${RED}✘${NC} That one's taken too.\n"
              rm -f "$CREATE_LOG"
            else
              printf "\r  ${RED}✘${NC} Creating project failed:\n"
              echo ""
              sed 's/^/      /' "$CREATE_LOG"
              echo ""
              hint "Fix the issue above and re-run: make setup"
              rm -f "$CREATE_LOG"
              ERROR_HANDLED=true; exit 1
            fi
          done
        else
          printf "\r  ${RED}✘${NC} Creating project failed:\n"
          echo ""
          sed 's/^/      /' "$CREATE_LOG"
          echo ""
          hint "Fix the issue above and re-run: make setup"
          rm -f "$CREATE_LOG"
          ERROR_HANDLED=true; exit 1
        fi
      fi
      if ! $DRY_RUN; then gcloud config set project "$GCP_PROJECT" 2>/dev/null; fi

      # ── Link billing ──
      echo ""
      info "Linking a billing account to the project."
      hint "Google Cloud requires a credit card on file before it lets you"
      hint "run services. You won't be charged unless you go way beyond"
      hint "what this project uses."
      echo ""
      BILLING_ACCOUNTS=$(sim "012345-6789AB-CDEF01	My Billing Account" gcloud billing accounts list --filter=open=true --format="value(name,displayName)" 2>/dev/null || true)
      BILLING_LINKED=false

      if [ -n "$BILLING_ACCOUNTS" ]; then
        ACCOUNT_COUNT=$(echo "$BILLING_ACCOUNTS" | wc -l | tr -d ' ')
        if [ "$ACCOUNT_COUNT" -eq 1 ]; then
          BILLING_ID=$(echo "$BILLING_ACCOUNTS" | awk '{print $1}')
          BILLING_NAME=$(echo "$BILLING_ACCOUNTS" | cut -f2-)
          spin "Linking billing ($BILLING_NAME)" \
            gcloud billing projects link "$GCP_PROJECT" --billing-account="$BILLING_ID"
          BILLING_LINKED=true
        else
          info "You have multiple billing accounts. Which one should I use?"
          echo "$BILLING_ACCOUNTS" | awk -F'\t' '{printf "    %d) %s\n", NR, $2}'
          echo ""
          while true; do
            read -rp "  Pick a number [1]: " BILLING_CHOICE
            BILLING_CHOICE="${BILLING_CHOICE:-1}"
            BILLING_ID=$(echo "$BILLING_ACCOUNTS" | sed -n "${BILLING_CHOICE}p" | awk '{print $1}')
            if [ -n "$BILLING_ID" ]; then break; fi
            fail "Enter a number from the list above"
          done
          spin "Linking billing" \
            gcloud billing projects link "$GCP_PROJECT" --billing-account="$BILLING_ID"
          BILLING_LINKED=true
        fi
      fi

      if $BILLING_LINKED; then
        ok "Billing linked to $GCP_PROJECT"
      else
        warn "I couldn't find a billing account on your Google account."
        echo ""
        hint "You'll need to add one in the browser. Here's what to do:"
        hint "  1. Open: https://console.cloud.google.com/billing/create"
        hint "  2. Add a credit card and create a billing account"
        hint "  3. Then open: https://console.cloud.google.com/billing/linkedaccount?project=$GCP_PROJECT"
        hint "  4. Link the billing account to your project"
        echo ""
        read -rp "  Press Enter when that's done (or Ctrl-C to quit and come back later)..."
        ok "Continuing"
      fi
    fi

    # Save progress so a crash doesn't lose the project
    write_env
  else
    ok "GCP project: $GCP_PROJECT"
  fi

  # ── B) Drive folder ─────────────────────────────────────────────────
  if [ -z "${DRIVE_FOLDER_ID:-}" ]; then
    echo ""
    printf "  ${BOLD}B) Google Drive folder${NC}\n"
    hint "Which folder in your Google Drive should we watch for changes?"
    hint "Open it in your browser and copy the URL from the address bar."
    hint "It'll look like: drive.google.com/drive/folders/1aBcD..."
    echo ""
    hint "You can paste the full URL — I'll extract the folder ID from it."
    echo ""
    while true; do
      read -rp "  Drive folder URL: " FOLDER_INPUT
      if DRIVE_FOLDER_ID=$(extract_drive_folder_id "$FOLDER_INPUT"); then
        ok "Got it: ${DRIVE_FOLDER_ID:0:20}..."
        break
      fi
      fail "I couldn't find a folder ID in that."
      hint "A specific folder is required (My Drive root can't be shared with the bot)."
      hint "Open drive.google.com, navigate to the folder, and paste the URL."
    done
    write_env
  else
    ok "Drive folder: ${DRIVE_FOLDER_ID:0:20}..."
  fi

  # ── C) Git repository ───────────────────────────────────────────────
  if [ -z "${GIT_REPO_URL:-}" ]; then
    echo ""
    printf "  ${BOLD}C) Git repository${NC}\n"
    hint "Synced files will be stored in a Git repository — think of it as a"
    hint "shared folder with full version history for every file."
    echo ""
    read -rp "  Do you already have a GitHub/GitLab repo for this? [y/N]: " HAS_REPO

    if [[ "${HAS_REPO:-}" =~ ^[Yy] ]]; then
      hint "Go to the repo page and copy the HTTPS URL (starts with https://)."
      echo ""
      while true; do
        read -rp "  Repo URL: " GIT_REPO_URL
        if [[ "$GIT_REPO_URL" =~ ^https:// ]]; then
          break
        fi
        fail "That doesn't look like an HTTPS URL"
        hint "Example: https://github.com/yourname/your-repo.git"
      done
    else
      # Check if gh CLI is available
      if command -v gh &>/dev/null; then
        GH_USER=$(sim "dryrunuser" gh api user --jq .login 2>/dev/null || true)
        if [ -z "$GH_USER" ] && ! $DRY_RUN; then
          info "Let's log in to GitHub so I can create a repo for you."
          gh auth login
          GH_USER=$(gh api user --jq .login 2>/dev/null || true)
        fi
        if [ -n "$GH_USER" ]; then
          # Check for org memberships
          GH_ORGS=$(gh api user/orgs --jq '.[].login' 2>/dev/null || true)
          GH_OWNER="$GH_USER"
          if [ -n "$GH_ORGS" ]; then
            echo ""
            info "Where should the repo live?"
            echo "    1) $GH_USER (personal)"
            ORG_NUM=2
            while IFS= read -r org; do
              [ -z "$org" ] && continue
              echo "    $ORG_NUM) $org"
              ORG_NUM=$((ORG_NUM + 1))
            done <<< "$GH_ORGS"
            echo ""
            read -rp "  Pick a number [1]: " OWNER_CHOICE
            OWNER_CHOICE="${OWNER_CHOICE:-1}"
            if ! [[ "$OWNER_CHOICE" =~ ^[0-9]+$ ]] || [ "$OWNER_CHOICE" -lt 1 ]; then
              fail "Invalid input — using personal account ($GH_USER)"
              OWNER_CHOICE=1
            fi
            if [ "$OWNER_CHOICE" -gt 1 ]; then
              GH_OWNER=$(echo "$GH_ORGS" | sed -n "$((OWNER_CHOICE - 1))p")
              if [ -z "$GH_OWNER" ]; then
                fail "Number out of range — using personal account ($GH_USER)"
                GH_OWNER="$GH_USER"
              fi
            fi
          fi

          if [ "$GH_OWNER" = "$GH_USER" ]; then
            info "I'll create a GitHub repo for you (logged in as @$GH_USER)."
          else
            info "I'll create a GitHub repo under $GH_OWNER."
          fi
          SUGGESTED_REPO="drive-sync"
          echo ""
          hint "What should the repo be called? Press Enter for \"$SUGGESTED_REPO\"."
          read -rp "  Repo name [$SUGGESTED_REPO]: " REPO_NAME
          REPO_NAME="${REPO_NAME:-$SUGGESTED_REPO}"

          # Idempotent — skip if repo already exists (e.g. re-run after crash)
          if gh repo view "$GH_OWNER/$REPO_NAME" &>/dev/null; then
            GIT_REPO_URL="https://github.com/$GH_OWNER/$REPO_NAME.git"
            ok "Repo already exists: $GIT_REPO_URL"
          else
            if [ "$GH_OWNER" = "$GH_USER" ]; then
              spin "Creating private repo $GH_OWNER/$REPO_NAME" \
                gh repo create "$REPO_NAME" --private --clone=false --description "Drive files version-controlled in git"
            else
              spin "Creating private repo $GH_OWNER/$REPO_NAME" \
                gh repo create "$GH_OWNER/$REPO_NAME" --private --clone=false --description "Drive files version-controlled in git"
            fi
            GIT_REPO_URL="https://github.com/$GH_OWNER/$REPO_NAME.git"
            ok "Created: $GIT_REPO_URL"
          fi
        else
          fail "Couldn't log in to GitHub. Let's enter a repo URL instead."
          hint "Create one at https://github.com/new, then paste the HTTPS URL."
          echo ""
          while true; do
            read -rp "  Repo URL: " GIT_REPO_URL
            if [[ "$GIT_REPO_URL" =~ ^https:// ]]; then break; fi
            fail "That doesn't look like an HTTPS URL"
          done
        fi
      else
        hint "Create a repo at one of these, then paste the HTTPS URL:"
        hint "  GitHub: https://github.com/new"
        hint "  GitLab: https://gitlab.com/projects/new"
        echo ""
        while true; do
          read -rp "  Repo URL: " GIT_REPO_URL
          if [[ "$GIT_REPO_URL" =~ ^https:// ]]; then break; fi
          fail "That doesn't look like an HTTPS URL"
        done
      fi
    fi

    GIT_BRANCH="main"
    write_env
  else
    ok "Git repo: $GIT_REPO_URL"
  fi

  # ── D) Git personal access token ────────────────────────────────────
  echo ""
  printf "  ${BOLD}D) Access token${NC}\n"
  hint "The sync bot needs a password (called a \"token\") to push files"
  hint "to your repo. You create this on GitHub/GitLab and paste it here."
  hint "It's stored securely in Google Cloud — never saved to disk."
  echo ""

  # Detect host to give specific instructions
  GIT_HOST=$(detect_git_host "$GIT_REPO_URL")
  if [ "$GIT_HOST" = "github" ]; then
    info "Open this page to create a token:"
    hint "  https://github.com/settings/tokens?type=beta"
    echo ""
    hint "Then:"
    hint "  1. Click \"Generate new token\""
    hint "  2. Name: anything (e.g. \"drive-sync-bot\")"
    hint "  3. Repository access → \"Only select repositories\" → pick your repo"
    hint "  4. Permissions → Repository permissions → Contents → \"Read and write\""
    hint "  5. Click \"Generate token\" and copy it"
  elif [ "$GIT_HOST" = "gitlab" ]; then
    info "Open your repo → Settings → Access Tokens, then:"
    hint "  1. Create a project access token"
    hint "  2. Scope: write_repository"
    hint "  3. Copy the token"
  elif [ "$GIT_HOST" = "bitbucket" ]; then
    info "Open your repo → Repository settings → Access tokens, then:"
    hint "  1. Create a token with \"Repositories: Write\" permission"
    hint "  2. Copy the token"
  else
    hint "Create a personal access token with push (write) access to your repo."
    hint "Check your git host's docs for how to generate one."
  fi

  echo ""
  hint "Paste it below. (Nothing will appear as you type — that's normal.)"
  while true; do
    read -rsp "  Token: " GIT_TOKEN_VALUE
    echo ""
    if [ -n "$GIT_TOKEN_VALUE" ]; then break; fi
    fail "Token can't be empty — try pasting again"
  done

  # Quick validation — check if the token can access the repo
  info "Checking if the token works..."
  if [ "$GIT_HOST" = "github" ] && OWNER_REPO=$(extract_github_owner_repo "$GIT_REPO_URL"); then
    OWNER="${OWNER_REPO%% *}"
    REPO="${OWNER_REPO##* }"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -H "Authorization: token $GIT_TOKEN_VALUE" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$OWNER/$REPO" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
      ok "Token works — has access to $OWNER/$REPO"
    elif [ "$HTTP_CODE" = "000" ]; then
      hint "Couldn't reach GitHub to verify (no internet?). Continuing anyway."
    else
      warn "Token doesn't seem to have access to $OWNER/$REPO."
      hint "Double-check that you gave it \"Contents: Read and write\" permission"
      hint "for the right repo."
      read -rp "  Continue anyway? [Y/n]: " CONTINUE
      if [[ "${CONTINUE:-Y}" =~ ^[Nn] ]]; then ERROR_HANDLED=true; exit 1; fi
    fi
  elif [ "$GIT_HOST" = "gitlab" ] && [[ "$GIT_REPO_URL" =~ gitlab\.com[:/](.+) ]]; then
    GL_PATH="${BASH_REMATCH[1]%.git}"
    GL_PATH="${GL_PATH%/}"
    GL_ENCODED=$(echo "$GL_PATH" | sed 's/\//%2F/g')
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -H "PRIVATE-TOKEN: $GIT_TOKEN_VALUE" \
      "https://gitlab.com/api/v4/projects/$GL_ENCODED" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
      ok "Token works — has access to $GL_PATH"
    elif [ "$HTTP_CODE" != "000" ]; then
      warn "Token doesn't seem to have access to $GL_PATH."
      read -rp "  Continue anyway? [Y/n]: " CONTINUE
      if [[ "${CONTINUE:-Y}" =~ ^[Nn] ]]; then ERROR_HANDLED=true; exit 1; fi
    fi
  else
    ok "Token received (couldn't auto-verify for this host)"
  fi

  GIT_TOKEN_SECRET="git-token"

  # ── Final .env write ──
  write_env
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  echo ""
  ok "Configuration saved to .env"
  hint "Your settings live in this file. You can edit it anytime and re-run setup."
fi

# Validate essentials are present
: "${GCP_PROJECT:?GCP_PROJECT is required in .env}"
: "${DRIVE_FOLDER_ID:?DRIVE_FOLDER_ID is required in .env}"
: "${GIT_REPO_URL:?GIT_REPO_URL is required in .env}"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 3 — GCP setup + deploy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase "3/4" "Building your sync service"

info "Now I'll set up the cloud infrastructure. Here's what happens:"
hint "  1. Sign in to Google Cloud (if not already)"
hint "  2. Turn on the services your sync bot needs"
hint "  3. Deploy the sync function that watches your Drive"
hint "  4. Store your access token securely"
hint ""
hint "This is mostly automated — sit back and watch the checkmarks."

# ── Auth ──
CURRENT_ACCOUNT=$(sim "dryrun@example.com" gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)
ADC_FILE="${CLOUDSDK_CONFIG_DIR:-$HOME/.config/gcloud}/application_default_credentials.json"

if $DRY_RUN; then
  ok "Authenticated as dryrun@example.com [dry-run]"
elif $AUTO; then
  if [ -n "$CURRENT_ACCOUNT" ]; then
    ok "Authenticated as $CURRENT_ACCOUNT"
  elif [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]; then
    ok "Using service account from GOOGLE_APPLICATION_CREDENTIALS"
  else
    fail "No GCP authentication found."
    hint "Before running in non-interactive mode, authenticate with one of:"
    hint "  gcloud auth login && gcloud auth application-default login"
    hint "  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json"
    ERROR_HANDLED=true; exit 1
  fi
  if [ ! -f "$ADC_FILE" ] && [ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]; then
    fail "Terraform needs application-default credentials."
    hint "Run: gcloud auth application-default login"
    ERROR_HANDLED=true; exit 1
  fi
elif $FULL_AUTH_DONE; then
  # Already did both gcloud auth + ADC during project creation in Phase 2
  CURRENT_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)
  ok "Authenticated as $CURRENT_ACCOUNT"
else
  # Interactive: guide through login if needed
  if [ -n "$CURRENT_ACCOUNT" ] && gcloud auth print-access-token &>/dev/null; then
    ok "Authenticated as $CURRENT_ACCOUNT"
  else
    echo ""
    info "First, let's sign in to Google Cloud."
    hint "A browser window will open — sign in with whatever Google account"
    hint "you want to use."
    echo ""
    gcloud auth login
    ok "Signed in to Google Cloud"
  fi
  if [ ! -f "$ADC_FILE" ]; then
    echo ""
    info "One more sign-in — same account, different permission."
    hint "This lets the setup tool create resources on your behalf."
    gcloud auth application-default login
    ok "Setup tool authorized"
  fi
fi

if ! $DRY_RUN; then
  gcloud config set project "$GCP_PROJECT" 2>/dev/null
  # Set quota project for ADC to avoid "quota exceeded" / "API not enabled" errors
  gcloud auth application-default set-quota-project "$GCP_PROJECT" 2>/dev/null || true
fi
ok "Active project: $GCP_PROJECT"

# ── APIs ──
echo ""
info "Turning on Google Cloud services..."
hint "Your sync bot needs Drive access, Cloud Functions, a database, and a few"
hint "other services. This is a one-time setup."

# Check if a key API is already enabled to skip the slow enable-all call
APIS_NEEDED=true
if ! $DRY_RUN; then
  if gcloud services list --enabled --filter="name:cloudfunctions.googleapis.com" --format="value(name)" 2>/dev/null | grep -q cloudfunctions; then
    APIS_NEEDED=false
    ok "All required services already enabled"
  fi
fi

if $APIS_NEEDED; then
  spin "Enabling services (Drive, Cloud Functions, Firestore, etc.)" \
    gcloud services enable \
      cloudfunctions.googleapis.com \
      cloudscheduler.googleapis.com \
      firestore.googleapis.com \
      secretmanager.googleapis.com \
      drive.googleapis.com \
      cloudbuild.googleapis.com \
      run.googleapis.com \
      artifactregistry.googleapis.com \
      siteverification.googleapis.com
  ok "All services enabled"
fi

# ── Source bucket ──
BUCKET="${GCP_PROJECT}-functions-source"
BUCKET_EXISTS=false
if $DRY_RUN; then
  BUCKET_EXISTS=true
elif gcloud storage buckets describe "gs://$BUCKET" &>/dev/null; then
  BUCKET_EXISTS=true
fi

if $BUCKET_EXISTS; then
  ok "Storage bucket ready"
else
  spin "Creating storage bucket for function code" \
    gcloud storage buckets create "gs://$BUCKET" --location=us-central1
  ok "Storage bucket created"
fi

# ── Deploy ──
echo ""
info "Deploying the sync function..."
hint "This packages your code, sets up the database, creates a service account,"
hint "and deploys everything to Google Cloud. It takes a few minutes."

# Skip if terraform state already has the sync handler (previous deploy succeeded).
# deploy.sh is idempotent via terraform, but it's slow — no need to re-run if nothing changed.
DEPLOY_NEEDED=true
if ! $DRY_RUN && [ -f "$ROOT_DIR/infra/terraform.tfstate" ]; then
  if terraform -chdir="$ROOT_DIR/infra" output -raw sync_handler_url &>/dev/null; then
    DEPLOY_NEEDED=false
    ok "Already deployed from a previous run"
    hint "To force a fresh deploy: make deploy"
  fi
fi

if $DEPLOY_NEEDED; then
  spin "Deploying to Google Cloud (this takes a few minutes)" \
    "$SCRIPT_DIR/deploy.sh"
  ok "Sync function deployed"
fi

# ── Git token ──
echo ""
info "Storing your access token securely..."
hint "The token is saved in Google Cloud Secret Manager — an encrypted vault."
hint "Your sync function will read it from there when pushing to git."

SECRET_NAME="${GIT_TOKEN_SECRET:-git-token}"

store_token() {
  local token_val="$1"
  local tmpfile; tmpfile=$(mktemp)
  printf '%s' "$token_val" > "$tmpfile"
  spin "Saving token to Secret Manager" \
    gcloud secrets versions add "$SECRET_NAME" --data-file="$tmpfile"
  rm -f "$tmpfile"
}

HAS_VERSION=$(sim "" gcloud secrets versions list "$SECRET_NAME" --limit=1 --format="value(name)" 2>/dev/null || true)

if $DRY_RUN; then
  if [ -n "$GIT_TOKEN_VALUE" ]; then
    ok "Would store git token in Secret Manager [dry-run]"
    GIT_TOKEN_VALUE=""
  else
    ok "Would check/store git token [dry-run]"
  fi
elif [ -n "$GIT_TOKEN_VALUE" ]; then
  store_token "$GIT_TOKEN_VALUE"
  ok "Token stored securely"
  GIT_TOKEN_VALUE=""
elif [ -n "$HAS_VERSION" ]; then
  ok "Token already stored from a previous run"
elif $AUTO; then
  warn "No git token found in Secret Manager and none provided."
  hint "Set GIT_TOKEN_VALUE env var and re-run, or add manually:"
  hint "  echo -n 'YOUR_TOKEN' | gcloud secrets versions add $SECRET_NAME --data-file=-"
else
  echo ""
  info "No git token found in Secret Manager."
  # Give host-specific instructions (same as Section D in Phase 2)
  TOKEN_HOST=$(detect_git_host "${GIT_REPO_URL:-}")
  if [ "$TOKEN_HOST" = "github" ]; then
    hint "Create one at: https://github.com/settings/tokens?type=beta"
    hint "  → Generate new token → Repository access: your repo"
    hint "  → Permissions → Contents → Read and write → Generate"
  elif [ "$TOKEN_HOST" = "gitlab" ]; then
    hint "Open your repo → Settings → Access Tokens"
    hint "  → Scope: write_repository → Create"
  else
    hint "Create a personal access token with push (write) access to your repo."
  fi
  echo ""
  hint "Paste it below. (Nothing will appear as you type — that's normal.)"
  while true; do
    read -rsp "  Token: " GIT_TOKEN_VALUE
    echo ""
    if [ -n "$GIT_TOKEN_VALUE" ]; then break; fi
    fail "Token can't be empty"
  done
  store_token "$GIT_TOKEN_VALUE"
  ok "Token stored securely"
  GIT_TOKEN_VALUE=""
fi

echo ""
ok "Infrastructure is ready!"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 4 — Connect everything
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
phase "4/4" "Connecting everything"

SYNC_URL=$(sim "https://drive-sync-handler-abc123-uc.a.run.app" terraform -chdir="$ROOT_DIR/infra" output -raw sync_handler_url 2>/dev/null || echo "")
SETUP_URL=$(sim "https://drive-sync-setup-abc123-uc.a.run.app/setup-watch" terraform -chdir="$ROOT_DIR/infra" output -raw setup_watch_url 2>/dev/null || echo "")
SA_EMAIL=$(sim "drive-sync@my-project-12345.iam.gserviceaccount.com" terraform -chdir="$ROOT_DIR/infra" output -raw service_account_email 2>/dev/null || echo "")
REGION="${REGION:-us-central1}"

if [ -z "$SYNC_URL" ] || [ -z "$SA_EMAIL" ]; then
  fail "Couldn't read deployment outputs. Run 'make deploy' first."
  ERROR_HANDLED=true; exit 1
fi

# ── Extra OAuth scopes ──────────────────────────────────────────────
# gcloud's default scopes don't include Drive or Site Verification,
# so we need one more sign-in to automate the remaining steps.
EXTRA_SCOPES_TOKEN=""

if $DRY_RUN; then
  EXTRA_SCOPES_TOKEN="dry-run-token"
  ok "Would request extra OAuth scopes [dry-run]"
elif $AUTO; then
  # In auto mode we can't open a browser. Use existing ADC if available
  # (may lack siteverification/drive scopes — API calls will fail gracefully).
  EXTRA_SCOPES_TOKEN=$(gcloud auth application-default print-access-token 2>/dev/null || true)
  if [ -z "$EXTRA_SCOPES_TOKEN" ]; then
    warn "No application-default credentials found — manual steps will be printed."
    EXTRA_SCOPES_TOKEN=""
  fi
else
  # Try existing ADC credentials first — skip re-auth if scopes are already granted.
  # Network failure here clears the token, triggering re-auth below — acceptable tradeoff.
  EXTRA_SCOPES_TOKEN=$(gcloud auth application-default print-access-token 2>/dev/null || true)
  if [ -n "$EXTRA_SCOPES_TOKEN" ]; then
    # Test both required scopes: Drive and Site Verification
    DRIVE_OK=false
    SITEV_OK=false
    DRIVE_TEST=$(curl -s "https://www.googleapis.com/drive/v3/about?fields=user" \
      -H "Authorization: Bearer $EXTRA_SCOPES_TOKEN" \
      -H "X-Goog-User-Project: $GCP_PROJECT" 2>/dev/null || echo "")
    if echo "$DRIVE_TEST" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'user' in d" 2>/dev/null; then
      DRIVE_OK=true
    fi
    SITEV_TEST=$(curl -s "https://www.googleapis.com/siteVerification/v1/webResource" \
      -H "Authorization: Bearer $EXTRA_SCOPES_TOKEN" \
      -H "X-Goog-User-Project: $GCP_PROJECT" 2>/dev/null || echo "")
    # Site Verification returns {"items":[]} on success or 403 when scope is missing
    if ! echo "$SITEV_TEST" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('error',{}).get('code') == 403" 2>/dev/null; then
      SITEV_OK=true
    fi
    if $DRIVE_OK && $SITEV_OK; then
      ok "Using existing credentials"
    else
      EXTRA_SCOPES_TOKEN=""  # insufficient scopes — need re-auth
    fi
  fi
  if [ -z "$EXTRA_SCOPES_TOKEN" ]; then
    echo ""
    info "One more sign-in to let me automate the remaining steps."
    hint "Same account, same browser — just click Accept. This grants"
    hint "permission to share Drive folders and verify domains on your behalf."
    echo ""
    if gcloud auth application-default login \
      --scopes="openid,email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/siteverification,https://www.googleapis.com/auth/drive" \
      2>/dev/null; then
      gcloud auth application-default set-quota-project "$GCP_PROJECT" 2>/dev/null || true
      EXTRA_SCOPES_TOKEN=$(gcloud auth application-default print-access-token 2>/dev/null || true)
      ok "Permissions granted"
    else
      warn "Sign-in was cancelled — I'll print manual instructions instead."
    fi
  fi
fi

# Helper: call a Google API and return the JSON response.
# Sets API_OK=true if the response looks like a success (has expected fields, no error).
api_call() {
  local method="$1" url="$2" body="${3:-}"
  API_RESULT=""
  API_OK=false
  if [ -z "$EXTRA_SCOPES_TOKEN" ]; then return 1; fi
  if [ "$EXTRA_SCOPES_TOKEN" = "dry-run-token" ]; then API_OK=true; return 0; fi

  # X-Goog-User-Project is required — ADC tokens use gcloud's default OAuth
  # client, and most Google APIs refuse requests unless billing is routed
  # to the user's project.
  if [ -n "$body" ]; then
    API_RESULT=$(curl -s -X "$method" "$url" \
      -H "Authorization: Bearer $EXTRA_SCOPES_TOKEN" \
      -H "Content-Type: application/json" \
      -H "X-Goog-User-Project: $GCP_PROJECT" \
      -d "$body" 2>/dev/null || echo "")
  else
    API_RESULT=$(curl -s -X "$method" "$url" \
      -H "Authorization: Bearer $EXTRA_SCOPES_TOKEN" \
      -H "X-Goog-User-Project: $GCP_PROJECT" 2>/dev/null || echo "")
  fi

  # Check for success: response has a known success field and no error
  if echo "$API_RESULT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert 'error' not in d
assert any(k in d for k in ('id','token','owners','items','kind'))
" 2>/dev/null; then
    API_OK=true
  fi
}

# Helper: extract a JSON field.
json_field() {
  echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin).get('$2',''))" 2>/dev/null || echo ""
}

json_error() {
  echo "$1" | python3 -c "
import sys,json
d=json.load(sys.stdin)
e=d.get('error',{})
print(e.get('message','') if isinstance(e,dict) else str(e))
" 2>/dev/null || echo ""
}

# ── Step 1: Domain verification ─────────────────────────────────────
echo ""
printf "  ${BOLD}Step 1: Verify webhook URL${NC}\n"
hint "Google needs to confirm you own the webhook URL before it will"
hint "send Drive change notifications to it."

VERIFY_DONE=false

if [ -n "$EXTRA_SCOPES_TOKEN" ] && [ "$EXTRA_SCOPES_TOKEN" != "dry-run-token" ]; then
  # Check if already verified
  api_call GET "https://www.googleapis.com/siteVerification/v1/webResource"
  ALREADY_VERIFIED=false
  if [ -n "$API_RESULT" ]; then
    if echo "$API_RESULT" | SYNC_URL="$SYNC_URL" python3 -c "
import sys,json,os
items=json.load(sys.stdin).get('items',[])
target=os.environ['SYNC_URL']
domain=target.split('/')[2]  # extract domain from URL
for i in items:
  site=i.get('site',{}).get('identifier','')
  if target.startswith(site) or domain in site:
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
      ALREADY_VERIFIED=true
    fi
  fi

  if $ALREADY_VERIFIED; then
    ok "Domain already verified"
    VERIFY_DONE=true
  else
    # Get verification token
    api_call POST "https://www.googleapis.com/siteVerification/v1/token" \
      "{\"site\":{\"type\":\"SITE\",\"identifier\":\"$SYNC_URL\"},\"verificationMethod\":\"FILE\"}"

    SITE_TOKEN=$(json_field "$API_RESULT" "token")

    if [ -n "$SITE_TOKEN" ]; then
      # Update function to serve the verification file
      spin "Setting verification token on function" \
        gcloud run services update drive-sync-handler \
          --region="$REGION" \
          --project="$GCP_PROJECT" \
          --update-env-vars "GOOGLE_VERIFICATION_TOKEN=$SITE_TOKEN" \
          --quiet

      # Save to .env for future deploys
      if grep -q "^GOOGLE_VERIFICATION_TOKEN=" "$ENV_FILE" 2>/dev/null; then
        # Portable sed -i (macOS requires '' arg, GNU doesn't)
        if sed --version 2>/dev/null | grep -q GNU; then
          sed -i "s|^GOOGLE_VERIFICATION_TOKEN=.*|GOOGLE_VERIFICATION_TOKEN=\"$SITE_TOKEN\"|" "$ENV_FILE"
        else
          sed -i '' "s|^GOOGLE_VERIFICATION_TOKEN=.*|GOOGLE_VERIFICATION_TOKEN=\"$SITE_TOKEN\"|" "$ENV_FILE"
        fi
      else
        echo "GOOGLE_VERIFICATION_TOKEN=\"$SITE_TOKEN\"" >> "$ENV_FILE"
      fi

      # Wait for update to propagate, then verify (retry a few times)
      for attempt in 1 2 3; do
        sleep $((attempt * 5))
        api_call POST \
          "https://www.googleapis.com/siteVerification/v1/webResource?verificationMethod=FILE" \
          "{\"site\":{\"type\":\"SITE\",\"identifier\":\"$SYNC_URL\"}}"

        if $API_OK; then
          ok "Domain verified"
          VERIFY_DONE=true
          break
        fi
        [ "$attempt" -lt 3 ] && info "Waiting for function to update... (attempt $attempt/3)"
      done

      if ! $VERIFY_DONE; then
        ERR=$(json_error "$API_RESULT")
        warn "Automatic verification failed${ERR:+: $ERR}"
      fi
    else
      ERR=$(json_error "$API_RESULT")
      warn "Couldn't get verification token${ERR:+: $ERR}"
      hint "The siteverification scope may not have been granted."
    fi
  fi
elif $DRY_RUN; then
  ok "Would verify domain [dry-run]"
  VERIFY_DONE=true
fi

if ! $VERIFY_DONE && ! $DRY_RUN; then
  echo ""
  warn "Automatic verification didn't work. Complete manually:"
  hint "  1. Open: https://search.google.com/search-console"
  hint "  2. Add property → URL prefix → paste:"
  echo -e "       ${BOLD}$SYNC_URL${NC}"
  hint "  3. Choose \"HTML file\" verification → click Verify"
  echo ""
  if ! $AUTO; then
    read -rp "  Press Enter after completing verification..." _
  fi
fi

# ── Step 2: Share Drive folder ──────────────────────────────────────
echo ""
printf "  ${BOLD}Step 2: Share Drive folder with the bot${NC}\n"

SHARE_DONE=false

if [ -n "$EXTRA_SCOPES_TOKEN" ] && [ "$EXTRA_SCOPES_TOKEN" != "dry-run-token" ]; then
  # Grant the service account Editor access to the Drive folder.
  # If already shared, the API returns an error caught below.
  api_call POST \
    "https://www.googleapis.com/drive/v3/files/${DRIVE_FOLDER_ID}/permissions?sendNotificationEmail=false" \
    "{\"type\":\"user\",\"role\":\"writer\",\"emailAddress\":\"$SA_EMAIL\"}"

  if $API_OK; then
    ok "Drive folder shared with bot ($SA_EMAIL)"
    SHARE_DONE=true
  else
    ERR=$(json_error "$API_RESULT")
    # "already has access" is fine
    if echo "$ERR" | grep -qi "already"; then
      ok "Bot already has access to the Drive folder"
      SHARE_DONE=true
    else
      warn "Couldn't share automatically${ERR:+: $ERR}"
    fi
  fi
elif $DRY_RUN; then
  ok "Would share Drive folder [dry-run]"
  SHARE_DONE=true
fi

if ! $SHARE_DONE && ! $DRY_RUN; then
  hint "Share manually:"
  hint "  1. Open your Drive folder in the browser"
  hint "  2. Share → paste:"
  echo -e "       ${BOLD}$SA_EMAIL${NC}"
  hint "  3. Set to Editor → uncheck Notify → Share"
  echo ""
  if ! $AUTO; then
    read -rp "  Press Enter after sharing..." _
  fi
fi

# ── Step 3: Start watching ──────────────────────────────────────────
echo ""
printf "  ${BOLD}Step 3: Starting Drive sync${NC}\n"
hint "Telling Google Drive to send change notifications to your function"
hint "and running an initial sync of existing files."

WATCH_DONE=false

if $DRY_RUN; then
  ok "Would initialize watch channel [dry-run]"
  WATCH_DONE=true
else
  IDENTITY_TOKEN=$(gcloud auth print-identity-token --audiences="$SETUP_URL" 2>/dev/null || true)
  # Fallback without --audiences if token doesn't look like a JWT
  if [ -z "$IDENTITY_TOKEN" ] || [[ ! "$IDENTITY_TOKEN" =~ ^ey ]]; then
    IDENTITY_TOKEN=$(gcloud auth print-identity-token 2>/dev/null || true)
  fi
  if [ -z "$IDENTITY_TOKEN" ] || [[ ! "$IDENTITY_TOKEN" =~ ^ey ]]; then
    warn "Couldn't get identity token."
    hint "Run manually:"
    echo -e "    curl -X POST \"${SETUP_URL}?initial_sync=true\" \\\\"
    echo -e "      -H \"Authorization: bearer \$(gcloud auth print-identity-token)\""
  else
    # Retry loop — domain verification can take time to propagate, and the
    # Cloud Function may cold-start slowly on the first call.
    WATCH_MAX=3
    WATCH_HTTP="000"
    WATCH_RESULT=""
    WATCH_STATUS=""
    WATCH_ATTEMPT=0

    while [ "$WATCH_ATTEMPT" -lt "$WATCH_MAX" ]; do
      WATCH_ATTEMPT=$((WATCH_ATTEMPT + 1))
      WATCH_LOG=$(mktemp)
      HTTP_LOG=$(mktemp)
      curl -s --max-time 120 -o "$WATCH_LOG" -w "%{http_code}" \
        -X POST "${SETUP_URL}?initial_sync=true" \
        -H "Authorization: bearer $IDENTITY_TOKEN" \
        >"$HTTP_LOG" 2>/dev/null &
      CURL_PID=$!; CURL_S=0
      while kill -0 "$CURL_PID" 2>/dev/null; do
        if [ "$WATCH_ATTEMPT" -eq 1 ]; then
          printf "\r  ⏳ Initializing watch channel and syncing existing files (%ds) " "$CURL_S"
        else
          printf "\r  ⏳ Retrying watch setup — attempt %d/%d (%ds) " "$WATCH_ATTEMPT" "$WATCH_MAX" "$CURL_S"
        fi
        sleep 1; CURL_S=$((CURL_S + 1))
      done
      wait "$CURL_PID" || true
      WATCH_HTTP=$(cat "$HTTP_LOG" 2>/dev/null)
      WATCH_HTTP="${WATCH_HTTP:-000}"
      WATCH_RESULT=$(cat "$WATCH_LOG" 2>/dev/null || echo "")
      rm -f "$WATCH_LOG" "$HTTP_LOG"
      printf "\r\033[2K"

      WATCH_STATUS=$(json_field "$WATCH_RESULT" "status")

      # Success — stop retrying
      if [ "$WATCH_STATUS" = "ok" ] || [ "$WATCH_STATUS" = "initialized" ]; then
        break
      fi

      # 403 = auth/permission issue — retrying won't help
      if [ "$WATCH_HTTP" = "403" ]; then
        break
      fi

      # Retryable failure — wait and try again
      if [ "$WATCH_ATTEMPT" -lt "$WATCH_MAX" ]; then
        DELAY=$((WATCH_ATTEMPT * 10))
        info "Attempt $WATCH_ATTEMPT didn't succeed (HTTP $WATCH_HTTP) — retrying in ${DELAY}s..."
        hint "Domain verification can take a moment to propagate."
        sleep "$DELAY"
      fi
    done

    # ── Handle result ──
    if [ "$WATCH_STATUS" = "ok" ] || [ "$WATCH_STATUS" = "initialized" ]; then
      SYNC_COUNT=$(json_field "$WATCH_RESULT" "initial_sync_count")
      ok "Watch channel initialized — Drive sync is active!"
      WATCH_DONE=true
      if [ -z "$SYNC_COUNT" ]; then
        warn "Sync could not run — lock may be held or initial_sync was skipped."
        hint "Wait a few minutes and re-run: make setup"
      elif [ "$SYNC_COUNT" != "0" ]; then
        ok "Initial sync complete: $SYNC_COUNT files committed to git"
      else  # SYNC_COUNT = "0"
        FILES_LISTED=$(echo "$WATCH_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('initial_sync_debug',{}).get('files_listed','?'))" 2>/dev/null || echo "?")
        if [ "$FILES_LISTED" = "0" ]; then
          warn "No files visible to the service account in Drive folder."
          hint "Check that the folder is shared with $SA_EMAIL (Editor role)."
          hint "Subfolders are traversed automatically — this likely means the"
          hint "service account doesn't have access to the folder."
        elif [ "$FILES_LISTED" != "?" ] && [ "$FILES_LISTED" -gt 0 ] 2>/dev/null; then
          # Files exist in Drive but Firestore thinks they're already tracked.
          # Likely stale state from a previous failed run — retry with force.
          hint "Drive has $FILES_LISTED files but state says already synced — retrying with reset..."
          WATCH_LOG=$(mktemp)
          HTTP_LOG=$(mktemp)
          curl -s --max-time 120 -o "$WATCH_LOG" -w "%{http_code}" \
            -X POST "${SETUP_URL}?initial_sync=true&force=true" \
            -H "Authorization: bearer $IDENTITY_TOKEN" \
            >"$HTTP_LOG" 2>/dev/null &
          CURL_PID=$!; CURL_S=0
          while kill -0 "$CURL_PID" 2>/dev/null; do
            printf "\r  ⏳ Re-syncing with fresh state (%ds) " "$CURL_S"
            sleep 1; CURL_S=$((CURL_S + 1))
          done
          wait "$CURL_PID" || true
          FORCE_HTTP=$(cat "$HTTP_LOG" 2>/dev/null)
          FORCE_HTTP="${FORCE_HTTP:-000}"
          WATCH_RESULT=$(cat "$WATCH_LOG" 2>/dev/null || echo "")
          rm -f "$WATCH_LOG" "$HTTP_LOG"
          printf "\r\033[2K"
          if [ "$FORCE_HTTP" != "200" ]; then
            warn "Force-resync failed (HTTP $FORCE_HTTP). Check function logs:"
            echo -e "    gcloud functions logs read drive-sync-setup-watch --region=$REGION --limit=20"
          else
            SYNC_COUNT=$(json_field "$WATCH_RESULT" "initial_sync_count")
            if [ -z "$SYNC_COUNT" ]; then
              warn "Sync lock was held during retry. Wait a few minutes and re-run: make setup"
            elif [ "$SYNC_COUNT" != "0" ]; then
              ok "Initial sync complete: $SYNC_COUNT files committed to git"
            else
              warn "Sync returned 0 files after reset. Check Cloud Function logs."
            fi
          fi
        else
          # FILES_LISTED is "?" — debug data missing (old function code?) or parse error
          hint "No new files to sync (folder empty or already up-to-date)"
          hint "Debug: ${WATCH_RESULT:0:200}"
        fi
      fi

    elif [ "$WATCH_HTTP" = "403" ]; then
      CURRENT_USER=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || echo "YOUR_EMAIL")
      warn "Function rejected the request (HTTP 403)."
      hint "Your account ($CURRENT_USER) doesn't have permission to invoke the function."
      hint "Grant yourself access, then re-run:"
      echo ""
      echo -e "    gcloud run services add-iam-policy-binding drive-sync-setup-watch \\"
      echo -e "      --region=$REGION --project=$GCP_PROJECT \\"
      echo -e "      --member=\"user:$CURRENT_USER\" --role=roles/run.invoker"
      echo ""
      hint "Then: make setup"

    elif [ "$WATCH_HTTP" = "500" ]; then
      warn "Function returned an internal error after $WATCH_MAX attempts."
      hint "Check the logs to see what went wrong:"
      echo -e "    gcloud functions logs read drive-sync-setup-watch --region=$REGION --limit=20"
      echo ""
      hint "Then re-run: make setup"

    else
      ERR=$(json_error "$WATCH_RESULT")
      if [ -n "$ERR" ]; then
        warn "Watch setup failed: $ERR"
      elif [ "$WATCH_HTTP" != "000" ]; then
        warn "Watch setup failed (HTTP $WATCH_HTTP)."
      else
        warn "Watch setup failed — couldn't reach the function."
        hint "The function may still be deploying. Wait a minute and re-run: make setup"
      fi
      if [ "$WATCH_HTTP" != "000" ]; then
        hint "Check the logs for details:"
        echo -e "    gcloud functions logs read drive-sync-setup-watch --region=$REGION --limit=20"
      fi
    fi
  fi
fi

# ── Done ────────────────────────────────────────────────────────────
echo ""
if $VERIFY_DONE && $SHARE_DONE && $WATCH_DONE; then
  echo -e "  ${GREEN}${BOLD}Setup complete!${NC}"
  echo -e "  ${DIM}Any file added or edited in your Drive folder will automatically${NC}"
  echo -e "  ${DIM}appear as a commit in your git repo.${NC}"
else
  echo -e "  ${YELLOW}${BOLD}Almost done!${NC}"
  echo -e "  ${DIM}Complete the manual steps above, then re-run: make setup${NC}"
fi
echo ""
hint "Your configuration is saved in .env — edit anytime and re-run: make setup"

# ── Machine-readable summary for agent mode ──
if $AUTO; then
  STEPS_REMAINING=0
  $VERIFY_DONE || STEPS_REMAINING=$((STEPS_REMAINING + 1))
  $SHARE_DONE  || STEPS_REMAINING=$((STEPS_REMAINING + 1))
  $WATCH_DONE  || STEPS_REMAINING=$((STEPS_REMAINING + 1))

  echo ""
  echo "--- AGENT SUMMARY ---"
  echo "STATUS: success"
  echo "MANUAL_STEPS_REMAINING: $STEPS_REMAINING"
  echo "SYNC_HANDLER_URL: $SYNC_URL"
  echo "SERVICE_ACCOUNT: $SA_EMAIL"
  echo "SETUP_WATCH_URL: $SETUP_URL"

  if ! $VERIFY_DONE; then
    echo ""
    echo "PENDING: Domain verification"
    echo "  SEARCH_CONSOLE: https://search.google.com/search-console"
    echo "  SYNC_URL: $SYNC_URL"
  fi
  if ! $SHARE_DONE; then
    echo ""
    echo "PENDING: Share Drive folder with $SA_EMAIL (Editor)"
  fi

  TOKEN_STORED=false
  [ -n "${HAS_VERSION:-}" ] && TOKEN_STORED=true
  gcloud secrets versions list "${SECRET_NAME:-git-token}" --limit=1 --format="value(name)" 2>/dev/null | grep -q . && TOKEN_STORED=true
  if ! $TOKEN_STORED; then
    echo ""
    echo "PENDING: Git token not stored in Secret Manager"
    echo "  COMMAND: echo -n 'YOUR_TOKEN' | gcloud secrets versions add ${SECRET_NAME:-git-token} --data-file=-"
  fi
  echo ""
  echo "--- END AGENT SUMMARY ---"
fi

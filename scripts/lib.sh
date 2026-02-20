#!/usr/bin/env bash
# Pure helper functions for setup.sh — no side effects, easily testable.

# Map tool name → brew install argument
brew_pkg() {
  case "$1" in
    gcloud)    echo "--cask google-cloud-sdk" ;;
    terraform) echo "hashicorp/tap/terraform" ;;
    git)       echo "git" ;;
    zip)       echo "zip" ;;
    gh)        echo "gh" ;;
  esac
}

# Map tool name → apt package name (empty = not available via apt)
apt_pkg() {
  case "$1" in
    gcloud)    echo "" ;;
    terraform) echo "" ;;
    git)       echo "git" ;;
    zip)       echo "zip" ;;
    gh)        echo "" ;;
  esac
}

# Map tool name → manual install URL
install_url() {
  case "$1" in
    gcloud)    echo "https://cloud.google.com/sdk/docs/install" ;;
    terraform) echo "https://developer.hashicorp.com/terraform/install" ;;
    git)       echo "https://git-scm.com/downloads" ;;
    zip)       echo "your system package manager" ;;
    gh)        echo "https://cli.github.com" ;;
  esac
}

# Extract a Drive folder ID from a URL, bare ID, or "root" / "my-drive" keyword.
# Prints the folder ID on success, returns 1 on failure.
extract_drive_folder_id() {
  local input="$1"
  if [[ "$input" =~ /folders/([a-zA-Z0-9_-]+) ]]; then
    echo "${BASH_REMATCH[1]}"
  elif [[ "$input" =~ my-drive ]] || [[ "$input" == "root" ]]; then
    echo "root"
  elif [[ "$input" =~ ^[a-zA-Z0-9_-]{10,}$ ]]; then
    echo "$input"
  else
    return 1
  fi
}

# Validate a GCP project ID.  Returns 0 if valid, 1 otherwise.
validate_gcp_project_id() {
  [[ "$1" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]
}

# Detect git host from a repo URL. Prints: github, gitlab, bitbucket, or other.
detect_git_host() {
  local url="$1"
  if [[ "$url" =~ github\.com ]]; then
    echo "github"
  elif [[ "$url" =~ gitlab\.com ]]; then
    echo "gitlab"
  elif [[ "$url" =~ bitbucket\.org ]]; then
    echo "bitbucket"
  else
    echo "other"
  fi
}

# Extract owner/repo from a GitHub HTTPS URL.
# Prints "owner repo" on success, returns 1 on failure.
extract_github_owner_repo() {
  local url="$1"
  if [[ "$url" =~ github\.com[:/]([^/]+)/([^/.]+) ]]; then
    echo "${BASH_REMATCH[1]} ${BASH_REMATCH[2]%.git}"
  else
    return 1
  fi
}

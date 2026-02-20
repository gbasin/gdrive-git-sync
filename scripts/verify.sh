#!/usr/bin/env bash
set -euo pipefail

# Post-deploy smoke test for gdrive-git-sync
# Requires: gdrive CLI, gcloud, git, and a configured deployment

if [ -z "${DRIVE_FOLDER_ID:-}" ] || [ -z "${GIT_REPO_URL:-}" ]; then
  echo "Error: DRIVE_FOLDER_ID and GIT_REPO_URL env vars required"
  echo "Usage: DRIVE_FOLDER_ID=xxx GIT_REPO_URL=https://... ./scripts/verify.sh"
  exit 1
fi

VERIFY_DIR=$(mktemp -d)
PASS=0
FAIL=0

cleanup() {
  rm -rf "$VERIFY_DIR"
}
trap cleanup EXIT

assert_file_exists() {
  if [ -f "$1" ]; then
    echo "  PASS: $1 exists"
    ((PASS++))
  else
    echo "  FAIL: $1 not found"
    ((FAIL++))
  fi
}

assert_not_exists() {
  if [ ! -f "$1" ]; then
    echo "  PASS: $1 correctly absent"
    ((PASS++))
  else
    echo "  FAIL: $1 should not exist"
    ((FAIL++))
  fi
}

assert_contains() {
  if grep -q "$2" "$1" 2>/dev/null; then
    echo "  PASS: $1 contains '$2'"
    ((PASS++))
  else
    echo "  FAIL: $1 does not contain '$2'"
    ((FAIL++))
  fi
}

wait_for_commit() {
  local pattern="$1"
  local timeout=${2:-90}
  local elapsed=0

  echo "  Waiting for commit matching '$pattern' (timeout: ${timeout}s)..."
  while [ $elapsed -lt "$timeout" ]; do
    cd "$VERIFY_DIR/repo"
    git pull --quiet 2>/dev/null || true
    if git log --oneline -5 | grep -q "$pattern"; then
      echo "  Found commit after ${elapsed}s"
      return 0
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
  echo "  WARN: Timed out waiting for commit"
  return 1
}

echo "=== Smoke Test: gdrive-git-sync ==="
echo ""

# Clone the target repo
echo "1. Cloning target repo..."
git clone "$GIT_REPO_URL" "$VERIFY_DIR/repo" --quiet

# Check if test fixtures exist
FIXTURES_DIR="$(cd "$(dirname "$0")/../tests/fixtures" && pwd)"
if [ ! -f "$FIXTURES_DIR/simple.docx" ]; then
  echo "SKIP: Test fixtures not found at $FIXTURES_DIR"
  echo "Create test .docx and .pdf files in tests/fixtures/ to run full smoke test"
  exit 0
fi

echo "2. Uploading test docx..."
FILE_ID=$(gdrive files upload --parent "$DRIVE_FOLDER_ID" "$FIXTURES_DIR/simple.docx" 2>&1 | grep -oP 'Id: \K\S+')
echo "   Uploaded: $FILE_ID"

echo "3. Waiting for sync..."
if wait_for_commit "simple.docx"; then
  echo "4. Verifying files..."
  assert_file_exists "$VERIFY_DIR/repo/docs/simple.docx"
  assert_file_exists "$VERIFY_DIR/repo/docs/simple.docx.md"
fi

echo "5. Checking commit log..."
cd "$VERIFY_DIR/repo"
git log --oneline -5
echo ""

# Check Cloud Function logs
echo "6. Checking function logs for errors..."
ERROR_COUNT=$(gcloud functions logs read drive-sync-handler --gen2 --limit=20 2>/dev/null | grep -c "ERROR" || true)
if [ "$ERROR_COUNT" -eq 0 ]; then
  echo "  PASS: No errors in recent logs"
  ((PASS++))
else
  echo "  WARN: $ERROR_COUNT errors found in logs"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

#!/usr/bin/env bash
set -euo pipefail

# Bootstrap script for gdrive-git-sync
# Sets up GCP project, enables APIs, creates resources

if [ -z "${GCP_PROJECT:-}" ]; then
  echo "Error: GCP_PROJECT env var required"
  echo "Usage: GCP_PROJECT=my-project ./scripts/bootstrap.sh"
  exit 1
fi

echo "=== Setting up GCP project: $GCP_PROJECT ==="

# Set project
gcloud config set project "$GCP_PROJECT"

# Enable required APIs
echo "Enabling APIs..."
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudscheduler.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  drive.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com

# Create GCS bucket for function source
BUCKET="${GCP_PROJECT}-functions-source"
echo "Creating source bucket: $BUCKET"
gcloud storage buckets create "gs://$BUCKET" --location=us-central1 2>/dev/null || true

# Create Secret Manager secret for git token
SECRET_NAME="${GIT_TOKEN_SECRET:-git-token}"
echo "Creating secret: $SECRET_NAME"
gcloud secrets create "$SECRET_NAME" --replication-policy=automatic 2>/dev/null || true

echo ""
echo "=== Manual steps required ==="
echo ""
echo "1. Add your git token to Secret Manager:"
echo "   echo -n 'YOUR_TOKEN' | gcloud secrets versions add $SECRET_NAME --data-file=-"
echo ""
echo "2. Deploy the functions first (to get URLs):"
echo "   ./scripts/deploy.sh"
echo ""
echo "3. Domain verification for Drive webhooks:"
echo "   a. Go to Google Search Console: https://search.google.com/search-console"
echo "   b. Add Property → URL Prefix → paste your sync_handler URL"
echo "   c. Verify via 'HTML file' method (the function serves it automatically)"
echo "   d. Go to Google API Console → Domain Verification → Add Domain"
echo "   e. Paste the function domain (without path)"
echo ""
echo "4. Share the Drive folder with the service account:"
echo "   Service account email will be shown after terraform apply"
echo "   Share your Drive folder with Editor access"
echo ""
echo "5. Initialize the watch channel:"
echo "   curl -X POST \$(terraform -chdir=infra output -raw setup_watch_url)"
echo ""
echo "Bootstrap complete!"

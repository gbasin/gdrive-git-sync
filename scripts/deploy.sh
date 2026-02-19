#!/usr/bin/env bash
set -euo pipefail

# Deploy script: zip functions, run terraform apply

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Packaging functions ==="
cd "$ROOT_DIR"

# Create zip of functions directory
rm -f functions_source.zip
cd functions
zip -r ../functions_source.zip . -x '__pycache__/*' '*.pyc'
cd "$ROOT_DIR"

echo "=== Running Terraform ==="
cd infra

# Upload source to GCS (bucket name from terraform var)
terraform init
terraform apply -auto-approve

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

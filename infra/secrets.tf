resource "google_secret_manager_secret" "git_token" {
  secret_id = var.git_token_secret

  replication {
    auto {}
  }
}

# Note: The secret VERSION must be created manually or via bootstrap.sh:
#   echo -n "ghp_YOUR_TOKEN" | gcloud secrets versions add git-token --data-file=-

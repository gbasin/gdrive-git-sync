resource "google_service_account" "sync" {
  account_id   = "drive-git-sync"
  display_name = "Drive Git Sync"
  description  = "Service account for Drive → Git sync Cloud Functions"
}

# Firestore access
resource "google_project_iam_member" "datastore_user" {
  project = var.gcp_project
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.sync.email}"
}

# Secret Manager access
resource "google_project_iam_member" "secret_accessor" {
  project = var.gcp_project
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.sync.email}"
}

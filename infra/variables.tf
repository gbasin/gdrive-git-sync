variable "gcp_project" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Functions"
  type        = string
  default     = "us-central1"
}

variable "drive_folder_id" {
  description = "Google Drive folder ID to monitor"
  type        = string
}

variable "git_repo_url" {
  description = "Git repository HTTPS URL"
  type        = string
}

variable "git_branch" {
  description = "Git branch to push to"
  type        = string
  default     = "main"
}

variable "git_token_secret" {
  description = "Secret Manager secret name for git auth token"
  type        = string
  default     = "git-token"
}

variable "exclude_paths" {
  description = "Comma-separated glob patterns to exclude"
  type        = string
  default     = ""
}

variable "skip_extensions" {
  description = "Comma-separated extensions to skip"
  type        = string
  default     = ".zip,.exe,.dmg,.iso"
}

variable "max_file_size_mb" {
  description = "Max file size in MB"
  type        = number
  default     = 100
}

variable "commit_author_name" {
  description = "Fallback commit author name"
  type        = string
  default     = "Drive Sync Bot"
}

variable "commit_author_email" {
  description = "Fallback commit author email"
  type        = string
  default     = "sync@example.com"
}

variable "firestore_collection" {
  description = "Firestore root collection name"
  type        = string
  default     = "drive_sync_state"
}

variable "docs_subdir" {
  description = "Subdirectory in git repo for synced files (empty = Drive folder name)"
  type        = string
  default     = ""
}

variable "google_verification_token" {
  description = "Google domain verification filename (e.g., googleXXXX.html)"
  type        = string
  default     = ""
}

variable "functions_source_bucket" {
  description = "GCS bucket for Cloud Functions source code"
  type        = string
}

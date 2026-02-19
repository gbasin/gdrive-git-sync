# Upload functions source to GCS
resource "google_storage_bucket_object" "functions_source" {
  name   = "functions-${filemd5("${path.module}/../functions_source.zip")}.zip"
  bucket = var.functions_source_bucket
  source = "${path.module}/../functions_source.zip"
}

# Common environment variables (without SYNC_HANDLER_URL to avoid circular ref)
locals {
  common_env = {
    GCP_PROJECT               = var.gcp_project
    DRIVE_FOLDER_ID           = var.drive_folder_id
    GIT_REPO_URL              = var.git_repo_url
    GIT_BRANCH                = var.git_branch
    GIT_TOKEN_SECRET          = var.git_token_secret
    EXCLUDE_PATHS             = var.exclude_paths
    SKIP_EXTENSIONS           = var.skip_extensions
    MAX_FILE_SIZE_MB          = tostring(var.max_file_size_mb)
    COMMIT_AUTHOR_NAME        = var.commit_author_name
    COMMIT_AUTHOR_EMAIL       = var.commit_author_email
    FIRESTORE_COLLECTION      = var.firestore_collection
    DOCS_SUBDIR               = var.docs_subdir
    GOOGLE_VERIFICATION_TOKEN = var.google_verification_token
  }
}

# === sync_handler: webhook receiver (public, unauthenticated) ===

resource "google_cloudfunctions2_function" "sync_handler" {
  name     = "drive-sync-handler"
  location = var.region

  build_config {
    runtime     = "python312"
    entry_point = "sync_handler"

    source {
      storage_source {
        bucket = var.functions_source_bucket
        object = google_storage_bucket_object.functions_source.name
      }
    }
  }

  service_config {
    available_memory                 = "1Gi"
    timeout_seconds                  = 300
    max_instance_count               = 1
    max_instance_request_concurrency = 1
    service_account_email            = google_service_account.sync.email

    environment_variables = local.common_env
  }
}

# Allow unauthenticated invocation (Drive webhooks can't authenticate)
resource "google_cloud_run_v2_service_iam_member" "sync_handler_public" {
  project  = var.gcp_project
  location = var.region
  name     = google_cloudfunctions2_function.sync_handler.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# === renew_watch: scheduled channel renewal ===

resource "google_cloudfunctions2_function" "renew_watch" {
  name     = "drive-sync-renew-watch"
  location = var.region

  build_config {
    runtime     = "python312"
    entry_point = "renew_watch"

    source {
      storage_source {
        bucket = var.functions_source_bucket
        object = google_storage_bucket_object.functions_source.name
      }
    }
  }

  service_config {
    available_memory      = "512Mi"
    timeout_seconds       = 120
    max_instance_count    = 1
    service_account_email = google_service_account.sync.email

    environment_variables = merge(local.common_env, {
      SYNC_HANDLER_URL = google_cloudfunctions2_function.sync_handler.url
    })
  }
}

# === setup_watch: one-time initialization ===

resource "google_cloudfunctions2_function" "setup_watch" {
  name     = "drive-sync-setup-watch"
  location = var.region

  build_config {
    runtime     = "python312"
    entry_point = "setup_watch"

    source {
      storage_source {
        bucket = var.functions_source_bucket
        object = google_storage_bucket_object.functions_source.name
      }
    }
  }

  service_config {
    available_memory      = "512Mi"
    timeout_seconds       = 300
    max_instance_count    = 1
    service_account_email = google_service_account.sync.email

    environment_variables = merge(local.common_env, {
      SYNC_HANDLER_URL = google_cloudfunctions2_function.sync_handler.url
    })
  }
}

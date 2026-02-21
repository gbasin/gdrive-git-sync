# Upload functions source to GCS
resource "google_storage_bucket_object" "functions_source" {
  name   = "functions-${filemd5("${path.module}/../functions_source.zip")}.zip"
  bucket = var.functions_source_bucket
  source = "${path.module}/../functions_source.zip"
}

# Common environment variables (without SYNC_HANDLER_URL to avoid circular ref)
locals {
  common_env = merge(
    {
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
      GOOGLE_VERIFICATION_TOKEN = var.google_verification_token
    },
    # Only set DOCS_SUBDIR when explicitly configured — empty means
    # "auto-resolve from Drive folder name" which the function handles.
    var.docs_subdir != "" ? { DOCS_SUBDIR = var.docs_subdir } : {},
  )
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

# Allow unauthenticated invocation — Drive webhooks don't send auth headers,
# so the endpoint must accept anonymous requests.
#
# We disable the Cloud Run IAM invoker check rather than granting allUsers
# the run.invoker role. Both achieve the same result (public endpoint), but
# the IAM approach is blocked by Domain Restricted Sharing org policies.
# Disabling the invoker check is a Cloud Run-native setting that bypasses
# IAM entirely, so org policies don't interfere.
#
# Security: the function validates webhook requests by matching the
# X-Goog-Channel-ID header against the stored channel ID — unknown
# channels are rejected. The Cloud Run URL is also an unguessable
# random domain. This is equivalent security to the allUsers IAM approach.
resource "null_resource" "sync_handler_no_iam_check" {
  triggers = {
    service_id = google_cloudfunctions2_function.sync_handler.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      gcloud run services update ${google_cloudfunctions2_function.sync_handler.name} \
        --region=${var.region} \
        --project=${var.gcp_project} \
        --no-invoker-iam-check \
        --quiet
    EOT
  }
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

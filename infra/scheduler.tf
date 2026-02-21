# Renew watch channel every 6 days (max channel lifetime is 7 days)
resource "google_cloud_scheduler_job" "renew_watch" {
  name             = "drive-sync-renew-watch"
  description      = "Renew Drive push notification channel"
  schedule         = "0 3 */6 * *" # Every 6 days at 3 AM
  time_zone        = "UTC"
  attempt_deadline = "300s"

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.renew_watch.url

    oidc_token {
      service_account_email = google_service_account.sync.email
      audience              = google_cloudfunctions2_function.renew_watch.service_config[0].uri
    }
  }
}

# Safety-net sync every 4 hours (catches missed notifications)
resource "google_cloud_scheduler_job" "safety_net" {
  name             = "drive-sync-safety-net"
  description      = "Periodic catchup sync in case notifications were missed"
  schedule         = "0 */4 * * *" # Every 4 hours
  time_zone        = "UTC"
  attempt_deadline = "300s"

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.sync_handler.url
    headers = {
      "X-Sync-Trigger-Secret" = var.sync_trigger_secret
    }

    # sync_handler is public, but scheduler requests are still authenticated
    # and include a shared trigger secret for channel-less sync safety.
    oidc_token {
      service_account_email = google_service_account.sync.email
      audience              = google_cloudfunctions2_function.sync_handler.service_config[0].uri
    }
  }
}

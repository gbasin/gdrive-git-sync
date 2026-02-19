output "sync_handler_url" {
  description = "URL of the sync webhook handler"
  value       = google_cloudfunctions2_function.sync_handler.url
}

output "renew_watch_url" {
  description = "URL of the watch renewal function"
  value       = google_cloudfunctions2_function.renew_watch.url
}

output "setup_watch_url" {
  description = "URL of the setup/init function"
  value       = google_cloudfunctions2_function.setup_watch.url
}

output "service_account_email" {
  description = "Service account email (share Drive folder with this)"
  value       = google_service_account.sync.email
}

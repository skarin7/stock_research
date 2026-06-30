output "vm_ip" {
  description = "Static IP of the Compute Engine VM."
  value       = google_compute_address.vm_ip.address
}

output "ssh_command" {
  description = "SSH into the VM."
  value       = "gcloud compute ssh stock@${var.job_name}-vm --zone=${var.vm_zone}"
}

output "chat_webhook_url" {
  description = "Register with Telegram after nginx+TLS is up: python scripts/set_webhook.py <url>"
  value       = "https://${google_compute_address.vm_ip.address}/telegram/webhook"
}

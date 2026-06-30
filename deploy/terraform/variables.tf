# ── GCP target ────────────────────────────────────────────────────────────────
variable "project_id" {
  type        = string
  description = "GCP project ID."
  default     = "gen-lang-client-0533266855"
}

variable "region" {
  type        = string
  description = "GCP region (asia-south1 = Mumbai, closest to NSE/BSE)."
  default     = "asia-south1"
}

variable "job_name" {
  type        = string
  description = "Resource naming base (VM name, firewall, IP, schedule)."
  default     = "stock-intelligence"
}

# ── Compute Engine VM ──────────────────────────────────────────────────────────
variable "vm_zone" {
  type    = string
  default = "asia-south1-a"
}

variable "vm_machine_type" {
  type    = string
  default = "e2-small"
}

variable "vm_ssh_pub_key" {
  type        = string
  description = "SSH public key for the 'stock' user on the VM."
  default     = ""
}

variable "enable_schedule" {
  type        = bool
  description = "Attach market-hours instance schedule to the VM. Set false to keep always-on."
  default     = true
}
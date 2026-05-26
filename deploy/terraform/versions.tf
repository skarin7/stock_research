terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }

  # Local state by default. For a shared/remote backend, uncomment and point at a
  # GCS bucket (cheap, versioned). Keep state private — it contains injected secrets.
  # backend "gcs" {
  #   bucket = "your-tfstate-bucket"
  #   prefix = "stock-intelligence"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

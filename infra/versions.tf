terraform {
  required_version = ">= 1.16.0"

  # Bucket and prefix are supplied at `terraform init` time via -backend-config
  # (see scripts/gcp/deploy.sh phase_init) so this stays project-agnostic.
  backend "gcs" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 8.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

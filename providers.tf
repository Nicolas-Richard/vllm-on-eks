terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Owner     = "nicolas.richard"
      Project   = "nico-sdbx"
      ManagedBy = "terraform-local"
    }
  }
}

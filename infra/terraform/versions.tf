terraform {
  required_version = ">= 1.8, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # For shared environments configure an encrypted, locked remote backend.
  # terraform init -backend-config=... after adding your organization's backend.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "supportops-ai", Environment = var.environment, ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" { state = "available" }

locals {
  name = "supportops-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
}

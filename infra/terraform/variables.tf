variable "region" {
  type    = string
  default = "eu-central-1"
}
variable "environment" {
  type    = string
  default = "staging"
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Use staging or production."
  }
}
variable "certificate_arn" {
  type        = string
  description = "Existing ACM certificate in this region; HTTPS is required."
}
variable "github_repository" {
  type    = string
  default = "dkkdark/SupportOps-AI"
}
variable "create_github_oidc_provider" {
  type        = bool
  default     = false
  description = "Set true only if the account does not already have GitHub's OIDC provider."
}
variable "image_digest" {
  type        = string
  default     = ""
  description = "Verified sha256:... digest in the created ECR repo. Keep desired_count=0 until image exists."
}
variable "desired_count" {
  type    = number
  default = 0
  validation {
    condition     = var.desired_count >= 0 && var.desired_count <= 3
    error_message = "Demo supports 0–3 tasks."
  }
}
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}
variable "db_engine_version" {
  type        = string
  default     = "17"
  description = "Select an available RDS PostgreSQL 17 minor in your region and verify pgvector support."
}
variable "llm_secret_arn" {
  type        = string
  default     = ""
  description = "Optional existing Secrets Manager secret holding only the LLM API key."
}
variable "llm_base_url" {
  type    = string
  default = "https://api.openai.com/v1"
}
variable "llm_model" {
  type    = string
  default = ""
}
variable "otel_image" {
  type        = string
  default     = ""
  description = "Optional ADOT collector image pinned by digest. Empty disables OTLP export; logs remain in CloudWatch."
}
variable "alarm_email" {
  type        = string
  default     = ""
  description = "Optional alarm recipient; SNS requires recipient confirmation."
}

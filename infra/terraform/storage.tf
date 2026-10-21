resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy     = jsonencode({ rules = [{ rulePriority = 1, description = "Keep latest 20 untagged images", selection = { tagStatus = "untagged", countType = "imageCountMoreThan", countNumber = 20 }, action = { type = "expire" } }] })
}
resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}
resource "aws_db_instance" "main" {
  identifier                      = local.name
  engine                          = "postgres"
  engine_version                  = var.db_engine_version
  instance_class                  = var.db_instance_class
  allocated_storage               = 20
  max_allocated_storage           = 40
  storage_type                    = "gp3"
  storage_encrypted               = true
  db_name                         = "supportops"
  username                        = "supportops"
  manage_master_user_password     = true
  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.db.id]
  publicly_accessible             = false
  backup_retention_period         = 7
  multi_az                        = var.environment == "production"
  deletion_protection             = var.environment == "production"
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${local.name}-final"
  auto_minor_version_upgrade      = true
  copy_tags_to_snapshot           = true
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
}
resource "aws_s3_bucket" "artifacts" { bucket_prefix = "${local.name}-artifacts-" }
resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-reports"
    status = "Enabled"
    filter { prefix = "evaluations/" }
    expiration { days = 90 }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}
resource "random_password" "api" {
  length  = 40
  special = false
}
resource "aws_secretsmanager_secret" "api" {
  name_prefix             = "${local.name}-api-key-"
  recovery_window_in_days = 7
}
resource "aws_secretsmanager_secret_version" "api" {
  secret_id     = aws_secretsmanager_secret.api.id
  secret_string = random_password.api.result
}

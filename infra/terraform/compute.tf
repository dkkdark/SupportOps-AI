resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = 14
}
resource "aws_cloudwatch_log_group" "telemetry" {
  name              = "/otel/${local.name}"
  retention_in_days = 14
}
resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}
locals {
  app_environment = [
    { name = "APP_ENV", value = "production" },
    { name = "DEMO_RELEASE", value = "true" },
    { name = "DEMO_AI_BUDGET_USD", value = "5" },
    { name = "FORWARDED_ALLOW_IPS", value = aws_vpc.main.cidr_block },
    { name = "ALLOW_DEMO_SIGNUP", value = "false" },
    { name = "SESSION_DAYS", value = "7" },
    { name = "DB_HOST", value = aws_db_instance.main.address },
    { name = "DB_USER", value = "supportops" },
    { name = "DB_NAME", value = "supportops" },
    { name = "ORDERS_API_URL", value = "http://127.0.0.1:8001" },
    { name = "MODEL_PROVIDER", value = "openai" },
    { name = "OPENAI_BASE_URL", value = var.llm_base_url },
    { name = "AGENT_MODEL", value = var.llm_model },
    { name = "VERIFIER_MODEL", value = var.llm_model },
    { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = var.otel_image == "" ? "" : "http://127.0.0.1:4318" },
    { name = "EMBEDDING_PROVIDER", value = "openai" },
    { name = "EMBEDDING_MODEL", value = var.embedding_model },
    { name = "PUBLIC_BASE_URL", value = var.public_base_url },
    { name = "STRIPE_STARTER_PRICE_ID", value = var.stripe_starter_price_id },
    { name = "STRIPE_GROWTH_PRICE_ID", value = var.stripe_growth_price_id },
    { name = "STRIPE_SCALE_PRICE_ID", value = var.stripe_scale_price_id },
    { name = "SMTP_HOST", value = var.smtp_host },
    { name = "SMTP_USER", value = var.smtp_user },
    { name = "SMTP_FROM", value = var.smtp_from },
    { name = "TURNSTILE_SITE_KEY", value = var.turnstile_site_key },
    { name = "LEGAL_BUSINESS_NAME", value = var.legal_business_name },
    { name = "LEGAL_CONTACT_EMAIL", value = var.legal_contact_email },
    { name = "LEGAL_TERMS_URL", value = var.legal_terms_url },
    { name = "LEGAL_PRIVACY_URL", value = var.legal_privacy_url },
    { name = "LEGAL_DOCUMENTS_APPROVED", value = tostring(var.legal_documents_approved) },
    { name = "STRIPE_AUTOMATIC_TAX", value = tostring(var.stripe_automatic_tax) },
    { name = "CODE_VERSION", value = var.image_digest }
  ]
  app_secrets = concat([{ name = "DB_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" }], var.llm_secret_arn == "" ? [] : [{ name = "OPENAI_API_KEY", valueFrom = var.llm_secret_arn }], var.stripe_secret_arn == "" ? [] : [{ name = "STRIPE_SECRET_KEY", valueFrom = var.stripe_secret_arn }], var.stripe_webhook_secret_arn == "" ? [] : [{ name = "STRIPE_WEBHOOK_SECRET", valueFrom = var.stripe_webhook_secret_arn }], var.smtp_password_secret_arn == "" ? [] : [{ name = "SMTP_PASSWORD", valueFrom = var.smtp_password_secret_arn }], var.turnstile_secret_arn == "" ? [] : [{ name = "TURNSTILE_SECRET_KEY", valueFrom = var.turnstile_secret_arn }])
  app_image   = var.image_digest == "" ? "${aws_ecr_repository.app.repository_url}:bootstrap" : "${aws_ecr_repository.app.repository_url}@${var.image_digest}"
  log_config  = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.app.name, awslogs-region = var.region, awslogs-stream-prefix = "ecs" } }
  collector_config = yamlencode({
    receivers  = { otlp = { protocols = { http = { endpoint = "0.0.0.0:4318" } } } }
    processors = { batch = {} }
    exporters = {
      awsxray = { region = var.region }
      awsemf  = { region = var.region, log_group_name = aws_cloudwatch_log_group.telemetry.name, namespace = "SupportOps", dimension_rollup_option = "NoDimensionRollup" }
    }
    service = { pipelines = {
      traces  = { receivers = ["otlp"], processors = ["batch"], exporters = ["awsxray"] }
      metrics = { receivers = ["otlp"], processors = ["batch"], exporters = ["awsemf"] }
    } }
  })
}
resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.otel_image == "" ? "512" : "1024"
  memory                   = var.otel_image == "" ? "1024" : "2048"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode(concat([
    {
      name             = "app", image = local.app_image, essential = true,
      portMappings     = [{ containerPort = 8000, protocol = "tcp" }],
      environment      = local.app_environment,
      secrets          = local.app_secrets,
      logConfiguration = local.log_config,
      healthCheck      = { command = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)\""], interval = 30, timeout = 5, retries = 3, startPeriod = 45 },
      linuxParameters  = { initProcessEnabled = true }
    },
    {
      name             = "indexer", image = local.app_image, essential = true,
      command          = ["python", "-m", "scripts.index_worker"],
      environment      = local.app_environment,
      secrets          = local.app_secrets,
      logConfiguration = local.log_config,
      dependsOn        = [{ containerName = "app", condition = "HEALTHY" }],
      linuxParameters  = { initProcessEnabled = true }
    },
    ], var.otel_image == "" ? [] : [{
      name    = "otel", image = var.otel_image, essential = false,
      command = ["--config=env:OTEL_CONFIG"], environment = [{ name = "OTEL_CONFIG", value = local.collector_config }], logConfiguration = local.log_config
  }]))
}
resource "aws_lb" "app" {
  name                       = local.name
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
}
resource "aws_lb_target_group" "app" {
  name                 = local.name
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 15
  health_check {
    path                = "/health/ready"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
resource "aws_ecs_service" "app" {
  name                               = local.name
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.app.arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 60
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }
  depends_on = [aws_lb_listener.https, aws_iam_role_policy.secrets]
  lifecycle {
    ignore_changes = [task_definition]
    precondition {
      condition     = var.desired_count == 0 || can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
      error_message = "Set a verified ECR image digest before starting tasks."
    }
  }
}

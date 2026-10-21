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
  app_image  = var.image_digest == "" ? "${aws_ecr_repository.app.repository_url}:bootstrap" : "${aws_ecr_repository.app.repository_url}@${var.image_digest}"
  log_config = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.app.name, awslogs-region = var.region, awslogs-stream-prefix = "ecs" } }
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
      name         = "app", image = local.app_image, essential = true,
      portMappings = [{ containerPort = 8000, protocol = "tcp" }],
      environment = [
        { name = "APP_ENV", value = "production" },
        { name = "DB_HOST", value = aws_db_instance.main.address },
        { name = "DB_USER", value = "supportops" },
        { name = "DB_NAME", value = "supportops" },
        { name = "ORDERS_API_URL", value = "http://127.0.0.1:8001" },
        { name = "MODEL_PROVIDER", value = var.llm_secret_arn == "" ? "baseline" : "openai_compatible" },
        { name = "LLM_BASE_URL", value = var.llm_base_url },
        { name = "LLM_MODEL", value = var.llm_model },
        { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = var.otel_image == "" ? "" : "http://127.0.0.1:4318" },
        { name = "CODE_VERSION", value = var.image_digest }
      ],
      secrets = concat([
        { name = "DB_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" },
        { name = "APP_API_KEY", valueFrom = aws_secretsmanager_secret.api.arn }
      ], var.llm_secret_arn == "" ? [] : [{ name = "LLM_API_KEY", valueFrom = var.llm_secret_arn }]),
      logConfiguration = local.log_config,
      healthCheck      = { command = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)\""], interval = 30, timeout = 5, retries = 3, startPeriod = 45 },
      dependsOn        = [{ containerName = "orders", condition = "HEALTHY" }],
      linuxParameters  = { initProcessEnabled = true }
    },
    {
      name             = "orders", image = local.app_image, essential = true,
      command          = ["uvicorn", "supportops.orders_api:app", "--host", "0.0.0.0", "--port", "8001"],
      logConfiguration = local.log_config,
      healthCheck      = { command = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/live', timeout=2)\""], interval = 10, timeout = 3, retries = 3, startPeriod = 10 }
    }
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

# Deployment

All configurations are prepared locally. No GitHub push, AWS deployment or Kubernetes deployment has been performed. Terraform validation does not prove regional capacity, IAM authorization, certificate ownership or a successful apply.

## AWS Fargate

The blueprint provisions two public ALB subnets, two private application/RDS subnets, one NAT gateway, HTTPS ALB, ECS cluster/service, ECR, encrypted RDS PostgreSQL with managed password, private/versioned S3 artifacts, Secrets Manager, CloudWatch logs/alarms and optional SNS email. The single NAT gateway is a demo cost/availability tradeoff; it is not a multi-AZ egress design. Production enables RDS Multi-AZ and deletion protection.

Prerequisites: an AWS account, Terraform 1.8+, AWS CLI, an ACM certificate in the selected region and a domain/DNS record matching that certificate. The default region is Frankfurt. Confirm the PostgreSQL minor version, instance availability and pgvector support in your account/region before applying.

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Set certificate_arn and choose a supported db_engine_version.
# Keep desired_count = 0 until a verified image is pushed and migrations run.
terraform init
terraform fmt -check
terraform validate
terraform plan -out=staging.tfplan
# Review costs, IAM, networking and the plan before creating resources:
terraform apply staging.tfplan
```

An apply creates billable resources even with `desired_count=0`. The bootstrap task references an image that does not exist until delivery; zero tasks prevent a failed boot. Retrieve the Terraform outputs and configure the GitHub environment below. Set DNS to the ALB and use the matching domain for `APP_URL`; the generated ALB hostname will not match your own ACM certificate.

RDS manages the database password in Secrets Manager. ECS injects `DB_PASSWORD`; `DB_HOST`, `DB_USER` and `DB_NAME` construct a TLS-required connection string in server memory. Users authenticate through business accounts and database-backed sessions; no shared application API key is used. Production session cookies require HTTPS. Fargate trusts forwarded client addresses only from the VPC CIDR; its security group accepts application traffic only from the ALB. Keep equivalent trusted-proxy restrictions when adding another ingress so login throttles receive real client addresses without trusting arbitrary headers. `ALLOW_DEMO_SIGNUP=false` disables sample business signup. Keep Terraform state encrypted, access-controlled and locked. Never commit tfstate/tfvars or print secrets in build logs.

The initial blueprint uses the RDS provisioning account for application tables/checkpoints. Before real production use, create a restricted runtime database role and separate migration credentials. Configure backup/restore procedures and secret-rotation restarts. Provisioning IAM is separate from the narrowly scoped GitHub deployment role.

## GitHub delivery

`ci.yml` runs tests against real PostgreSQL and Qdrant, baseline dev and holdout evaluation, JavaScript syntax checks, a container build, Compose smoke tests, Terraform validation and Helm checks. It uses controlled external responses for repeatability.

`deploy.yml` runs **only via manual workflow dispatch** and uses the GitHub `staging` environment. Protect that environment and restrict allowed branches in GitHub. OIDC trust is bound to the exact repository and environment; no permanent AWS key is required. If the AWS account already has GitHub’s OIDC provider, keep `create_github_oidc_provider=false`; otherwise create it once.

Configure these staging **variables** from Terraform outputs:

| Variable | Value |
| --- | --- |
| `AWS_REGION` | Target region |
| `AWS_DEPLOY_ROLE_ARN` | `deployment_role_arn` |
| `ECR_REPOSITORY` | Repository name, e.g. `supportops-staging` |
| `ECS_CLUSTER` / `ECS_SERVICE` | Cluster/service outputs |
| `APP_URL` | HTTPS URL with a certificate-matching hostname |
| `ARTIFACTS_BUCKET` | S3 bucket output |
| `AGENT_MODEL` / `VERIFIER_MODEL` / `OPENAI_BASE_URL` | Optional live acceptance overrides; defaults use official OpenAI endpoints and gpt-5.4-mini |

Configure `OPENAI_API_KEY` as a staging secret for optional bounded real-agent acceptance. A durable external Qdrant is required: configure `qdrant_url` and `qdrant_api_key_secret_arn`. Terraform passes the URL/key to the app and worker but does not provision the vector server; include its storage, backups and cost separately. Do not run Qdrant on a Fargate ephemeral volume. Runtime credentials are separate Secrets Manager ARNs: `llm_secret_arn`, Stripe secret/webhook ARNs, SMTP password ARN and Turnstile secret ARN. Set the public HTTPS URL, embedding/agent models, matching token prices, SMTP sender, Turnstile site key and reviewed legal URLs in Terraform variables. Stripe configuration is for a future paid release; leave it empty for the current demo. The task explicitly uses DEMO_RELEASE=true with a $5 account allowance. The example documents those fields. The task runs the application and a durable index worker; it does not run the synthetic orders service. A telemetry collector is optional.

For the very first deployment only, dispatch with `bootstrap=true`: this verifies the landing page and database readiness without requiring a nonexistent verified account. This is **not authenticated acceptance**. Register an operator-owned account on the resulting HTTPS service, confirm its email, and set `SMOKE_EMAIL` / `SMOKE_PASSWORD` as protected staging secrets. Then repeat with `bootstrap=false` (the default) for the full authenticated smoke. Subsequent releases require those secrets. Do not use the bootstrap option to skip acceptance of a public paid launch.

The dispatch reruns tests and baseline holdout, optionally runs a bounded real-model benchmark, builds and pushes a unique commit/run-tagged image, resolves its immutable digest, records rollback metadata, registers a task definition, runs Alembic as a one-off task, updates the service and waits for stability. The full smoke signs in with the verified staging account, creates and retains an isolated synthetic business, publishes and indexes its policy, reviews a grounded request and logs out. With live providers it consumes a small amount of real model usage. It does not require sample signup. Evidence is preserved in S3. The ECS circuit breaker handles unhealthy rollout; the workflow also restores the previous task definition after a failed rollout/smoke. Database downgrades are deliberately not automatic. Migration 002 is the initial conversion to business workspaces and is incompatible with the former single-business binary: take a consistent backup and plan a maintenance window for an existing installation. Do not automatically roll back to a pre-002 image against a converted database; restore the matching database/code backup instead. On the first deployment there may be no previously healthy version to restore; verify and repair prerequisites before retrying.

For release immutability, use image digests and retain old ECR images used by rollback. Only untagged images are subject to the included ECR lifecycle rule. Terraform ignores task-definition changes on the service so routine Terraform applies do not accidentally undo CI releases.

## Telemetry

CloudWatch receives ECS technical logs by default. Unhealthy targets, target 5xx responses and unhandled workflow failures have SNS-backed alarms; an email subscription must be confirmed by its recipient.

For traces and node-duration metrics, set `otel_image` to a tested ADOT image **pinned by digest**. Terraform adds a collector sidecar, configures OTLP HTTP on loopback, sends traces to X-Ray and metrics to CloudWatch EMF, and grants the task role only the required telemetry writes. Without it, OTLP export is disabled. The workspace monitoring page still shows each result’s stored workflow steps. This collector option has been schema-validated as task configuration, but has not been run in AWS.

## Cost and cleanup

Do not infer an infrastructure budget from the LLM cost panel. Calculate the actual configuration with the [AWS Pricing Calculator](https://calculator.aws/) before applying. ECS desired count zero stops tasks, but **RDS, ALB, NAT gateway/EIP, storage, backups, CloudWatch, S3, ECR and Secrets Manager can continue to incur charges**.

```bash
# From the same Terraform directory and state:
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
```

Review the destroy plan. Production RDS deletion protection must be deliberately disabled in configuration first. The artifact bucket is not force-deleted: export needed reports, remove/version-delete its objects through a reviewed cleanup, then retry destroy. Final RDS snapshots are retained and billed separately; remove them separately only after confirming they are no longer needed. A fixed final-snapshot name may need to be changed if a snapshot from an earlier teardown already exists. Secrets remain pending deletion for seven days. Check the AWS console for retained snapshots, buckets/versions, log data and other resources after teardown. No cleanup command is run automatically by this project.

## Optional Kubernetes / kind

The Helm chart deploys the API and index-worker with an **external PostgreSQL** business database and **external Qdrant** vector server. Historical SQL migrations require pgvector; live semantic search uses Qdrant. Configure `QDRANT_URL` and `QDRANT_API_KEY` in the existing Secret. The optional synthetic orders sidecar is disabled by default (`includeDemoOrders=false`). Enable it only for isolated development samples. No EKS cluster is provisioned.

```bash
kind create cluster --name supportops
docker build -t supportops:local .
kind load docker-image supportops:local --name supportops
kubectl create namespace supportops
# Create a local .env.k8s containing DATABASE_URL and MODEL_PROVIDER.
# Use a cluster-reachable DB hostname, not 127.0.0.1.
kubectl -n supportops create secret generic supportops-env --from-env-file=.env.k8s
helm lint infra/helm
helm upgrade --install supportops infra/helm --namespace supportops --set appEnv=development --wait --timeout 5m
# APP_ENV=production requires HTTPS for authentication cookies.
# For local kind-only HTTP testing set APP_ENV=development explicitly.
kubectl -n supportops port-forward service/supportops 8010:8000
```

The pre-install/pre-upgrade Job runs Alembic. The deployment uses startup/readiness/liveness probes, non-root containers, resource requests/limits, no privilege escalation and `maxUnavailable=0` rolling updates. Use a different immutable image tag for an update:

```bash
helm upgrade supportops infra/helm -n supportops --set image.tag=your-verified-tag --wait
kubectl -n supportops rollout status deployment/supportops
helm rollback supportops -n supportops --wait
# Remove the demo cluster only when its contents are no longer needed:
kind delete cluster --name supportops
```

The chart is a local deployment alternative. An EKS rollout needs ingress/TLS, workload identity, external secrets, managed storage, policy controls and its own cost/operations review.

## Qdrant migration and backups

See [Qdrant operations](qdrant.md). Migration 006 retains old vectors and creates an evidence registry. Stop app/worker, back up SQL/checkpoints, migrate and transfer cached vectors with `python -m scripts.transfer_vectors --apply --confirm-stopped`; this command never calls OpenAI. Sources without complete cached embeddings need an explicit owner-triggered paid reindex. Qdrant snapshots/storage must be backed up separately; test their restore alongside SQL. Health is available at `/health/vector`.

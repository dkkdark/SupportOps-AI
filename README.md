# SupportOps AI

A support platform for independent businesses. Each business registers its own workspace, uploads its policies and reply templates, and invites employees to handle customer requests. Knowledge, tickets, team access, metrics and review history are scoped to that business.

Replies use verified excerpts from the business’s current policies. Employees review and edit every draft before approving it. Approval saves the reply; sending it to a customer remains a separate action.

![SupportOps business registration](docs/preview.png)

[View an employee’s company workspace](docs/workspace-preview.png).

## Start locally

Requires Python 3.11+ (3.13 recommended). Node is optional, for JavaScript formatting.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
cp .env.example .env
python -m scripts.dev
```

Open [SupportOps AI](http://127.0.0.1:8010). API documentation: [OpenAPI](http://127.0.0.1:8010/docs). Stop both local services with `Ctrl+C`. The ignored `var/` directory holds the SQLite database, sessions and LangGraph checkpoints.

## Set up a business

1. **Create workspace**: enter your name, work email, business name and a password of at least 12 characters. You become the owner. New businesses start with no tickets or policies and one customizable reply template.
2. **Knowledge base → Upload file**: upload TXT, Markdown, PDF, DOCX or JSON. Alternatively, choose **Add policy** and paste a rule. Use a clear title and category. Include the subject in the policy text so related questions can find it.
3. **Reply templates**: create or edit wording, or import existing templates. Include `{{facts}}` exactly once. Other variables: `{{customer_name}}`, `{{business_name}}`, `{{agent_name}}`, `{{subject}}`, `{{ticket_id}}`. Match a category or use `other` as the default. A ticket can explicitly select a template.
4. **Team → Invite a teammate**: choose an email and role. Copy the single-use link and share it yourself. No email is sent automatically. The link expires after 7 days and only the specified email can accept it. Existing users enter their existing account password.
5. **New ticket**: enter a customer’s request. **Prepare a reply** retrieves only this business’s active rules and uses its template. Review the text, sources and checks. Edit as needed, then **Approve reply** or escalate.
6. **Settings**: update business details, create another business or switch between your workspaces. Each workspace has separate data and memberships.

| Role | Access |
| --- | --- |
| Owner | Business settings, policies, templates, tickets, audit history; invite/manage admins and agents |
| Admin | Business settings, policies, templates, tickets, audit history; invite/manage agents |
| Agent | Read business knowledge/templates, create and analyze tickets, review replies |

Removing membership immediately blocks that workspace’s API access, including existing sessions. Owners cannot remove themselves or be demoted through member management.

## Knowledge and templates

Uploads are limited to **2 MB and 100,000 extracted text characters**. PDFs must have selectable text, be unencrypted and have at most 50 pages. DOCX imports use document paragraphs. Parsing runs in a bounded subprocess; original uploaded files are not retained. Extracted text, versions, hashes and chunks are stored in the workspace database.

Open a policy to **Publish new version** or **Archive policy**. New versions close the previous effective interval; archived or future rules are excluded from current retrieval. Existing replies retain their original source and template snapshots.

Template imports accept a text document containing one template, or JSON:

```json
[
  {
    "name": "Friendly appointment reply",
    "category": "cancellation",
    "body": "Hi {{customer_name}},\n\n{{facts}}\n\nBest regards,\n{{agent_name}}\n{{business_name}}"
  }
]
```

Each import accepts up to 50 templates and validates the full batch before saving. Template edits use revision checks to avoid overwriting another person’s changes.

## What is implemented

- Business registration, sign-in/out, workspace switching, Argon2id password hashes, database-backed expiring sessions and CSRF protection.
- Membership roles, email-bound invitations, access revocation and workspace audit events.
- Tenant-scoped SQL queries, composite object keys/foreign keys, per-business idempotency keys and workflow threads.
- File/manual policy ingestion, chunking, version/effective-date filtering, archival and company-owned reply templates.
- Responsive English workspace with ticket queue, search, priorities, source inspection, draft editing, approval/escalation and monitoring.
- FastAPI, SQLAlchemy, Alembic, LangGraph interrupt/resume, SQLite quick start and PostgreSQL/pgvector support.
- Tests, a separate synthetic retail evaluation suite, Docker, CI, Terraform AWS blueprint and optional Helm chart.

`MODEL_PROVIDER=baseline` works offline without an LLM key. It retrieves current policy excerpts and inserts them into the selected template. The optional `openai_compatible` provider selects verified fact IDs and tone through a configured HTTP endpoint. Trusted code renders the facts; unsupported/missing selections get at most two attempts before a manual handoff.

Retrieval uses a **128-dimensional character-hash vector baseline** combined with lexical overlap and categories. This is not a pretrained semantic search model. Classification uses a small English/Russian keyword baseline; businesses can organize their policies/templates with custom categories and explicitly select templates. Representative questions and human review are necessary to assess usefulness for a new business.

## Sample workspace

**Explore a sample workspace** creates a separate synthetic business, Northstar Goods, with American customer names, sample policies and read-only fake order records. Each sample signup receives its own workspace. New real businesses never inherit this data or call this mock order integration. Sample signup is disabled in production unless `ALLOW_DEMO_SIGNUP=true` is explicitly configured.

`make demo` exports a standalone browser simulation on port 8011. It has no registration server or shared team persistence and is intended only for a sample walkthrough.

## Run checks

```bash
make test
make eval
make smoke  # requires the local API; creates a separate synthetic test business
npm run check:format
```

The development/holdout suite covers **100 synthetic retail cases**. Those results are displayed only in sample workspaces, and do not claim accuracy for another business’s policies. A real workspace shows that no business-specific evaluation dataset has been configured.

```bash
docker compose up --build -d
# http://127.0.0.1:8010
docker compose down
```

Compose starts PostgreSQL/pgvector, runs migrations and starts the app and sample orders service. The database has no host port; the app binds to loopback. Its named volume preserves data; `docker compose down -v` deletes that local database. Stop the Python preview before using the same port.

## Existing databases and deployment

Migration `002` converts the previous single-business schema and preserves its records in an unassigned `legacy-demo` archive. Newly registered users receive no membership in that archive. Existing local data was backed up under `var/backups/` before migration. Do not copy legacy records into a new customer’s workspace.

Development runs migrations automatically. Production requires a separate Alembic migration step and **HTTPS**, because session cookies are Secure. Migration `002` is forward-only: restore a consistent backup to return to the former schema. An old application image is not compatible with the new schema.

The platform runs locally and has multi-business authorization. Public-service operations still require email verification, account recovery/password reset, abuse controls/quotas, billing if desired, and data retention/deletion processes. SMTP, SSO, payment processing and real CRM/order/helpdesk integrations are not implemented. New workspaces answer from uploaded knowledge; live account/order questions need a human check. No outbound customer messages are sent.

Local application tests, browser workflows and synthetic evaluation have passed. Terraform/Helm/Compose configuration checks are available. Docker’s daemon is unavailable on the current machine, so real PostgreSQL/container execution is left to CI; AWS, Kubernetes and an external LLM have not been deployed or benchmarked.

Git remote: [dkkdark/SupportOps-AI](https://github.com/dkkdark/SupportOps-AI). Features are committed locally. **Nothing has been pushed or published.**

Further details: [architecture](docs/architecture.md), [deployment](docs/deployment.md), [runbook](docs/runbook.md), [evaluation](docs/evaluation.md), [verification](docs/verification.md).

"""End-to-end contracts for isolated businesses, team access and owned knowledge."""

from datetime import timedelta
from io import BytesIO
from zipfile import ZipFile
from fastapi.testclient import TestClient
from sqlalchemy import select
from supportops.config import Settings
from supportops.main import create_app
from supportops.db import Invitation, LoginSession, User, utcnow
from supportops.auth import digest

PASSWORD = "A long testing password 123!"
POLICY = {
    "title": "Appointment cancellation",
    "text": "Appointments can be cancelled at least 48 hours in advance without any cancellation fee.",
    "category": "cancellation",
}
TICKET = {
    "customer": "Emma Wilson",
    "email": "emma@example.test",
    "subject": "Appointment cancellation",
    "message": "What is your appointment cancellation fee?",
}


def account(c):
    r = c.get("/api/auth/me")
    assert r.status_code == 200, r.text
    a = r.json()
    c.headers.update({"X-CSRF-Token": a["csrf_token"]})
    if a["organization"]:
        c.headers["X-Workspace-ID"] = a["organization"]["id"]
    return a


def register(c, name="Acme Services", email="alex@acme.test"):
    r = c.post(
        "/api/auth/register",
        json={
            "name": "Alex Morgan",
            "email": email,
            "password": PASSWORD,
            "business_name": name,
        },
    )
    assert r.status_code == 201, r.text
    return account(c)


def make_ticket(c):
    r = c.post("/api/tickets", json=TICKET)
    assert r.status_code == 201, r.text
    return r.json()


def invitation(owner, email="james@acme.test", role="agent"):
    r = owner.post("/api/invitations", json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def accept(c, invite, email="james@acme.test"):
    return c.post(
        "/api/auth/accept-invite",
        json={
            "token": invite["token"],
            "email": email,
            "name": "James Miller",
            "password": PASSWORD,
        },
    )


def test_registration_is_empty_and_password_session_are_hashed(platform):
    a, b, _ = platform
    info = register(a)
    assert info["organization"]["role"] == "owner"
    assert info["organization"]["workflow_mode"] == "knowledge"
    assert a.get("/api/tickets").json() == a.get("/api/knowledge").json() == []
    assert len(a.get("/api/templates").json()) == 1
    assert a.get("/api/evaluation").json()["status"] == "not_run"
    assert b.get("/api/tickets").status_code == 401
    token = a.cookies.get("supportops_session")
    with a.app.state.sessions() as db:
        assert db.scalar(select(User)).password_hash.startswith("$argon2id$")
        assert db.get(LoginSession, digest(token)) is not None
        assert db.get(LoginSession, token) is None
    r = a.post("/api/auth/logout")
    assert r.status_code == 200
    assert a.get("/api/auth/me").status_code == 401
    r = a.post(
        "/api/auth/login", json={"email": "ALEX@ACME.TEST", "password": PASSWORD}
    )
    assert r.status_code == 200
    assert account(a)["organization"]["id"] == info["organization"]["id"]


def test_tenant_boundaries_cover_objects_actions_retrieval_and_metrics(platform):
    a, b, _ = platform
    aa = register(a)
    bb = register(b, "Bright Studios", "olivia@bright.test")
    source = a.post("/api/knowledge", json=POLICY).json()
    template = a.post(
        "/api/templates",
        json={
            "name": "Acme reply",
            "body": "ACME EXCLUSIVE: {{facts}}",
            "category": "cancellation",
        },
    ).json()
    t = make_ticket(a)
    assert b.get("/api/tickets").json() == []
    assert b.get("/api/knowledge").json() == []
    assert b.get("/api/metrics").json()["total"] == 0
    for path, body in [
        (f"/api/tickets/{t['id']}/analyze", {}),
        (
            f"/api/tickets/{t['id']}/review",
            {"decision": "approve", "text": "Stolen reply", "revision": 0},
        ),
        (f"/api/knowledge/{source['id']}/archive", {}),
    ]:
        assert b.post(path, json=body).status_code == 404
    assert b.get("/api/tickets/" + t["id"]).status_code == 404
    assert (
        b.patch(
            "/api/templates/" + template["id"],
            json={"name": "Stolen", "body": "{{facts}}", "revision": 1},
        ).status_code
        == 404
    )
    assert (
        b.post(
            "/api/tickets", json={**TICKET, "template_id": template["id"]}
        ).status_code
        == 422
    )
    result = b.post("/api/tickets/" + make_ticket(b)["id"] + "/analyze", json={}).json()
    assert result["status"] == "manual" and not result["result"]["sources"]
    assert (
        a.get(
            "/api/tickets", headers={"X-Workspace-ID": bb["organization"]["id"]}
        ).status_code
        == 409
    )
    assert (
        a.post(
            "/api/auth/switch", json={"organization_id": bb["organization"]["id"]}
        ).status_code
        == 404
    )
    r = a.post("/api/tickets/" + t["id"] + "/analyze", json={}).json()
    assert r["status"] == "review"
    assert r["result"]["thread_id"].startswith(aa["organization"]["id"] + ":")
    assert (
        "48 hours" in r["result"]["draft"] and "ACME EXCLUSIVE" in r["result"]["draft"]
    )


def test_idempotency_is_scoped_to_business(platform):
    a, b, _ = platform
    register(a)
    register(b, "Bright Studios", "olivia@bright.test")
    first = a.post(
        "/api/tickets", json=TICKET, headers={"Idempotency-Key": "shared-key"}
    ).json()
    second = b.post(
        "/api/tickets", json=TICKET, headers={"Idempotency-Key": "shared-key"}
    ).json()
    assert first["id"] != second["id"]
    assert (
        a.post(
            "/api/tickets", json=TICKET, headers={"Idempotency-Key": "shared-key"}
        ).json()["id"]
        == first["id"]
    )


def test_invite_agent_permissions_and_immediate_revocation(platform):
    a, _, employee = platform
    register(a)
    a.post("/api/knowledge", json=POLICY)
    invite = invitation(a)
    assert accept(employee, invite).status_code == 200
    person = account(employee)
    assert person["organization"]["role"] == "agent"
    assert a.get("/api/invitations").json()[0]["accepted"]
    assert "token" not in a.get("/api/invitations").text
    for path, body in [
        ("/api/knowledge", POLICY),
        ("/api/templates", {"name": "Fake", "body": "{{facts}}"}),
        ("/api/invitations", {"email": "x@y.test"}),
        ("/api/organization", {"name": "Changed", "industry": "Business"}),
    ]:
        method = employee.patch if path == "/api/organization" else employee.post
        assert method(path, json=body).status_code == 403
    assert employee.get("/api/members").status_code == 403
    assert employee.get("/api/audit").status_code == 403
    t = make_ticket(employee)
    result = employee.post("/api/tickets/" + t["id"] + "/analyze", json={}).json()
    r = employee.post(
        "/api/tickets/" + t["id"] + "/review",
        json={
            "decision": "approve",
            "text": result["result"]["draft"],
            "revision": result["revision"],
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert (
        employee.get("/api/tickets/" + t["id"]).json()["reviews"][0]["actor"]
        == "James Miller"
    )
    assert (
        a.patch(
            "/api/members/" + person["user"]["id"],
            json={"role": "agent", "active": False},
        ).status_code
        == 200
    )
    assert employee.get("/api/tickets").status_code == 403
    assert employee.get("/api/auth/me").json()["organization"] is None


def test_invite_is_email_bound_single_use_expiring_and_revocable(platform):
    a, b, c = platform
    register(a)
    invite = invitation(a)
    assert accept(b, invite, "someone@else.test").status_code == 403
    assert accept(b, invite).status_code == 200
    assert accept(c, invite).status_code == 410
    invite = invitation(a, "ava@acme.test")
    with a.app.state.sessions() as db:
        row = db.get(Invitation, invite["id"])
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert accept(c, invite, "ava@acme.test").status_code == 410
    invite = invitation(a, "noah@acme.test")
    assert a.post("/api/invitations/" + invite["id"] + "/revoke").status_code == 200
    assert accept(c, invite, "noah@acme.test").status_code == 410


def test_owner_admin_agent_role_rules(platform):
    a, admin, _ = platform
    owner = register(a)
    assert accept(admin, invitation(a, role="admin")).status_code == 200
    account(admin)
    assert admin.post("/api/knowledge", json=POLICY).status_code == 201
    assert (
        admin.post(
            "/api/invitations", json={"email": "other@acme.test", "role": "admin"}
        ).status_code
        == 403
    )
    assert (
        admin.patch(
            "/api/members/" + owner["user"]["id"],
            json={"role": "agent", "active": False},
        ).status_code
        == 403
    )
    assert (
        a.patch(
            "/api/members/" + owner["user"]["id"],
            json={"role": "agent", "active": False},
        ).status_code
        == 403
    )


def test_csrf_origin_session_expiry_and_workspace_switch(platform):
    a, _, _ = platform
    original = register(a)
    assert (
        a.post("/api/tickets", json=TICKET, headers={"X-CSRF-Token": ""}).status_code
        == 403
    )
    assert (
        a.post(
            "/api/tickets", json=TICKET, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert (
        a.post("/api/tickets", json=TICKET, headers={"X-Workspace-ID": ""}).status_code
        == 409
    )
    r = a.post(
        "/api/organizations", json={"name": "Second Business", "industry": "Consulting"}
    )
    assert r.status_code == 201
    assert (
        a.get("/api/tickets").status_code == 409
    )  # stale browser tab cannot act in another business
    second = account(a)
    assert second["organization"]["id"] != original["organization"]["id"]
    assert len(second["memberships"]) == 2
    assert (
        a.post(
            "/api/auth/switch", json={"organization_id": original["organization"]["id"]}
        ).status_code
        == 200
    )
    account(a)
    with a.app.state.sessions() as db:
        row = db.get(LoginSession, digest(a.cookies.get("supportops_session")))
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert a.get("/api/tickets").status_code == 401


def test_uploads_versions_archival_and_source_snapshot(platform):
    a, _, _ = platform
    register(a)

    def upload(name, data):
        return a.post(
            "/api/knowledge/upload",
            data={
                "title": "Appointment cancellation"
                if name != "appointments.docx"
                else "Rescheduling appointments",
                "category": "cancellation",
            },
            files={"file": (name, data)},
        )

    r = upload("policy.md", POLICY["text"].encode())
    assert r.status_code == 201, r.text
    first = r.json()
    t = make_ticket(a)
    analyzed = a.post("/api/tickets/" + t["id"] + "/analyze", json={}).json()
    assert analyzed["status"] == "review"
    r = a.post(
        "/api/knowledge",
        json={
            **POLICY,
            "key": first["key"],
            "version": "2",
            "text": "Appointment cancellation is free at least 72 hours in advance.",
        },
    )
    assert r.status_code == 201
    active = r.json()
    assert (
        a.post(
            "/api/knowledge", json={**POLICY, "key": first["key"], "version": "2"}
        ).status_code
        == 409
    )
    r = a.post("/api/tickets/" + make_ticket(a)["id"] + "/analyze", json={}).json()
    assert "72 hours" in r["result"]["draft"] and "48 hours" not in r["result"]["draft"]
    assert a.post("/api/knowledge/" + active["id"] + "/archive").status_code == 200
    assert (
        a.post("/api/tickets/" + make_ticket(a)["id"] + "/analyze", json={}).json()[
            "status"
        ]
        == "manual"
    )
    assert "48 hours" in a.get("/api/tickets/" + t["id"]).json()["result"]["draft"]
    assert upload("malware.exe", b"arbitrary executable").status_code == 415
    assert upload("too-large.txt", b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    assert upload("broken.pdf", b"%PDF broken").status_code == 422
    doc = BytesIO()
    with ZipFile(doc, "w") as z:
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:t>Customer appointments may be rescheduled by calling our office.</w:t></w:p></w:document>',
        )
    assert upload("appointments.docx", doc.getvalue()).status_code == 201


def test_templates_import_validation_revisions_and_single_pass(platform):
    a, _, _ = platform
    register(a)
    a.post("/api/knowledge", json=POLICY)
    r = a.post(
        "/api/templates/upload",
        files={
            "file": (
                "templates.json",
                b'[{"name":"Special reply","category":"cancellation","body":"Dear {{customer_name}}: {{facts}} / {{business_name}}"}]',
            )
        },
    )
    assert r.status_code == 201, r.text
    template = r.json()[0]
    assert (
        a.post(
            "/api/templates", json={"name": "No evidence", "body": "A made-up answer"}
        ).status_code
        == 422
    )
    assert (
        a.post(
            "/api/templates",
            json={"name": "Unknown variable", "body": "{{facts}} {{secret}}"},
        ).status_code
        == 422
    )
    count = len(a.get("/api/templates").json())
    assert (
        a.post(
            "/api/templates/upload",
            files={
                "file": (
                    "bad.json",
                    b'[{"name":"Valid","body":"{{facts}}"},{"name":"Invalid","body":"no evidence"}]',
                )
            },
        ).status_code
        == 422
    )
    assert len(a.get("/api/templates").json()) == count
    body = {
        "name": "Updated reply",
        "body": "HELLO {{facts}} {{business_name}}",
        "revision": 1,
    }
    assert a.patch("/api/templates/" + template["id"], json=body).status_code == 200
    assert a.patch("/api/templates/" + template["id"], json=body).status_code == 409
    ticket = a.post(
        "/api/tickets",
        json={**TICKET, "template_id": template["id"], "customer": "{{business_name}}"},
    ).json()
    result = a.post("/api/tickets/" + ticket["id"] + "/analyze", json={}).json()
    assert result["result"]["manifest"]["template"]["revision"] == 2
    assert result["result"]["draft"].startswith("HELLO")
    from supportops.content import render_template

    assert (
        render_template(
            "{{customer_name}} / {{facts}}",
            {"customer_name": "{{facts}}", "facts": "Safe"},
        )
        == "{{facts}} / Safe"
    )


def test_demo_signup_is_isolated_and_production_disabled(platform, tmp_path):
    a, b, _ = platform
    a.post("/api/auth/demo")
    aa = account(a)
    assert (
        a.post("/api/organizations", json={"name": "Real Business"}).status_code == 403
    )
    b.post("/api/auth/demo")
    bb = account(b)
    assert aa["organization"]["id"] != bb["organization"]["id"]
    assert len(a.get("/api/tickets").json()) == 8
    assert len(b.get("/api/tickets").json()) == 8
    # Migrate first: production intentionally does not modify schema on startup.
    from supportops.db import make_database
    from supportops.migration import migrate

    url = f"sqlite:///{tmp_path}/production.db"
    engine, _ = make_database(url)
    migrate(engine)
    engine.dispose()
    with TestClient(
        create_app(
            Settings(
                database_url=url, app_env="production", require_signup_captcha=False
            )
        )
    ) as prod:
        assert prod.post("/api/auth/demo").status_code == 404
        r = prod.post(
            "/api/auth/register",
            json={
                "name": "Alex Morgan",
                "email": "alex@prod.test",
                "password": PASSWORD,
                "business_name": "Production Business",
            },
        )
        assert r.status_code == 201
        assert (
            "secure" in r.headers["set-cookie"].lower()
            and "httponly" in r.headers["set-cookie"].lower()
        )


def test_auth_validation_redacts_password_and_throttles(platform):
    a, _, _ = platform
    r = a.post("/api/auth/register", json={"password": "secret-value"})
    assert r.status_code == 422 and "secret-value" not in r.text
    for _ in range(10):
        assert (
            a.post(
                "/api/auth/login",
                json={"email": "missing@example.test", "password": PASSWORD},
            ).status_code
            == 401
        )
    assert (
        a.post(
            "/api/auth/login",
            json={"email": "missing@example.test", "password": PASSWORD},
        ).status_code
        == 429
    )


def test_selectable_pdf_policy_can_prepare_a_grounded_reply(platform):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

    a, _, _ = platform
    register(a)
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 50 700 Td (Appointment cancellation is free at least 48 hours in advance.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    pdf = BytesIO()
    writer.write(pdf)
    r = a.post(
        "/api/knowledge/upload",
        data={"title": "PDF appointment policy"},
        files={"file": ("appointments.pdf", pdf.getvalue(), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    assert "48 hours" in r.json()["text"]
    result = a.post("/api/tickets/" + make_ticket(a)["id"] + "/analyze", json={}).json()
    assert result["status"] == "review" and "48 hours" in result["result"]["draft"]

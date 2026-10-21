"""Gmail configuration, encrypted SMTP and safe recovery-link resend behavior."""

import smtplib
import pytest
from sqlalchemy import select
from supportops.config import Settings
from supportops.mailer import email_status, deliver, check_connection
from supportops.accounts import send_token
from supportops.db import RecoveryToken
from tests.test_workspaces import register


def gmail(**changes):
    return Settings(
        smtp_host="smtp.gmail.com",
        smtp_port=changes.pop("smtp_port", 587),
        smtp_user="info.xenonimus@gmail.com",
        smtp_password="synthetic-app-password",
        smtp_from="SupportOps AI <info.xenonimus@gmail.com>",
        **changes,
    )


class SMTP:
    instances = []
    login_error = None

    def __init__(self, host, port, **options):
        self.host, self.port, self.options = host, port, options
        self.calls, self.messages = [], []
        SMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, **options):
        assert options["context"].check_hostname
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append("login")
        if SMTP.login_error:
            raise SMTP.login_error
        assert username == "info.xenonimus@gmail.com"
        assert password == "synthetic-app-password"

    def send_message(self, message, **options):
        self.calls.append("send")
        self.messages.append((message, options))
        return {}

    def noop(self):
        self.calls.append("noop")
        return 250, b"OK"


@pytest.fixture
def smtp(monkeypatch):
    SMTP.instances, SMTP.login_error = [], None
    monkeypatch.setattr("supportops.mailer.smtplib.SMTP", SMTP)
    monkeypatch.setattr("supportops.mailer.smtplib.SMTP_SSL", SMTP)
    return SMTP


def test_missing_gmail_password_never_connects_or_issues_tokens(platform, smtp):
    c, _, _ = platform
    info = register(c)
    cfg = gmail().model_copy(update={"smtp_password": ""})
    assert email_status(cfg)["missing"] == ["SMTP_PASSWORD"]
    assert not deliver(cfg, "emma@example.test", "Hello", "Body")
    assert not send_token(cfg, c.app.state.sessions, info["user"]["id"], "reset")
    assert not smtp.instances
    with c.app.state.sessions() as db:
        assert not db.scalars(select(RecoveryToken)).all()


def test_starttls_authentication_precedes_delivery_and_connection_check_sends_nothing(
    smtp,
):
    cfg = gmail()
    assert check_connection(cfg)["ok"]
    assert smtp.instances[-1].calls == ["ehlo", "starttls", "ehlo", "login", "noop"]
    assert not smtp.instances[-1].messages
    assert deliver(cfg, "emma@example.test", "Verify email", "Synthetic body")
    instance = smtp.instances[-1]
    assert instance.calls == ["ehlo", "starttls", "ehlo", "login", "send"]
    message, options = instance.messages[0]
    assert message["Reply-To"] == "info.xenonimus@gmail.com"
    assert message["Message-ID"] and message["Date"]
    assert options["from_addr"] == "info.xenonimus@gmail.com"
    assert options["to_addrs"] == ["emma@example.test"]


def test_ssl_and_invalid_tls_profiles(smtp):
    assert deliver(
        gmail(smtp_port=465, smtp_ssl=True, smtp_starttls=False),
        "emma@example.test",
        "Hello",
        "Body",
    )
    assert "context" in smtp.instances[-1].options
    assert "starttls" not in smtp.instances[-1].calls
    assert not email_status(gmail(smtp_port=465))["ready"]
    assert not email_status(gmail(smtp_ssl=True))["ready"]
    assert not email_status(gmail(smtp_starttls=False))["ready"]
    assert not email_status(
        gmail(app_env="production", public_base_url="http://insecure.test")
    )["ready"]


def test_auth_errors_and_header_injection_do_not_leak_secrets(smtp, caplog):
    smtp.login_error = smtplib.SMTPAuthenticationError(
        535, b"synthetic-app-password private-provider-details"
    )
    assert not deliver(gmail(), "emma@example.test", "Hello", "Body")
    assert "smtp_authentication_failed" in caplog.text
    assert "synthetic-app-password" not in caplog.text
    assert "private-provider-details" not in caplog.text
    assert check_connection(gmail())["code"] == "smtp_authentication_failed"
    before = len(smtp.instances)
    assert not deliver(
        gmail(), "emma@example.test\nBcc: third@example.test", "Hello", "Body"
    )
    assert not deliver(
        gmail(), "emma@example.test", "Hello\nBcc: third@example.test", "Body"
    )
    assert len(smtp.instances) == before


def test_failed_resend_keeps_old_link_and_redemption_revokes_other_links(
    platform, monkeypatch
):
    c, other, _ = platform
    info = register(c)
    sent = []
    monkeypatch.setattr(
        "supportops.accounts.deliver",
        lambda _, to, subject, body: sent.append(body) or len(sent) == 1,
    )
    cfg = gmail()
    assert send_token(cfg, c.app.state.sessions, info["user"]["id"], "reset")
    first = sent[0].split("/#reset/")[1].split()[0]
    assert not send_token(cfg, c.app.state.sessions, info["user"]["id"], "reset")
    second = sent[1].split("/#reset/")[1].split()[0]
    assert (
        other.post(
            "/api/auth/reset-password",
            json={"token": first, "password": "A new secure password 123!"},
        ).status_code
        == 200
    )
    assert (
        other.post(
            "/api/auth/reset-password",
            json={"token": second, "password": "Another secure password 123!"},
        ).status_code
        == 410
    )
    assert c.get("/api/auth/me").status_code == 401


def test_email_diagnostics_are_admin_only_and_never_include_credentials(platform):
    c, other, _ = platform
    register(c)
    assert other.get("/api/email/status").status_code == 401
    response = c.get("/api/email/status")
    assert response.status_code == 200
    assert "password" not in response.text.lower()
    assert response.json()["ready"] is False

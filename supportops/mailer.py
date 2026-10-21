"""Server-only SMTP transport and safe, non-network configuration diagnostics."""

from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
import logging
import re
import smtplib
import ssl
from urllib.parse import urlparse

EMAIL = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


def mailbox(value):
    if not value or "\r" in value or "\n" in value:
        return None
    address = parseaddr(value)[1]
    return address if EMAIL.fullmatch(address) else None


def email_status(config):
    missing = []
    host = config.smtp_host.strip().lower()
    sender = mailbox(config.smtp_from)
    if not host:
        missing.append("SMTP_HOST")
    if not sender:
        missing.append("SMTP_FROM")
    if config.smtp_user and not config.smtp_password:
        missing.append("SMTP_PASSWORD")
    if config.smtp_password and not config.smtp_user:
        missing.append("SMTP_USER")
    if config.smtp_ssl and config.smtp_starttls:
        missing.append("choose_SSL_or_STARTTLS")
    if config.smtp_port == 465 and not config.smtp_ssl:
        missing.append("SMTP_SSL_for_port_465")
    if config.app_env == "production" and not (config.smtp_ssl or config.smtp_starttls):
        missing.append("encrypted_SMTP_required")
    if (
        config.app_env == "production"
        and urlparse(config.public_base_url).scheme != "https"
    ):
        missing.append("HTTPS_PUBLIC_BASE_URL")
    if host == "smtp.gmail.com":
        if not config.smtp_user:
            missing.append("SMTP_USER")
        if not config.smtp_password:
            missing.append("SMTP_PASSWORD")
        if not (config.smtp_starttls or config.smtp_ssl):
            missing.append("Gmail_requires_TLS")
        if sender and sender.casefold() != config.smtp_user.casefold():
            missing.append("Gmail_sender_must_match_account")
    return {
        "ready": not missing,
        "provider": "gmail" if host == "smtp.gmail.com" else "smtp",
        "host": host,
        "port": config.smtp_port,
        "encryption": "ssl"
        if config.smtp_ssl
        else "starttls"
        if config.smtp_starttls
        else "none",
        "sender": config.smtp_from,
        "support_email": config.support_email,
        "missing": list(dict.fromkeys(missing)),
        "note": "Configuration only. No connection or email delivery has been verified.",
    }


@contextmanager
def connection(config):
    context = ssl.create_default_context()
    factory = smtplib.SMTP_SSL if config.smtp_ssl else smtplib.SMTP
    options = {"timeout": config.smtp_timeout_seconds}
    if config.smtp_ssl:
        options["context"] = context
    with factory(config.smtp_host, config.smtp_port, **options) as smtp:
        smtp.ehlo()
        if config.smtp_starttls:
            smtp.starttls(context=context)
            smtp.ehlo()
        if config.smtp_user:
            smtp.login(config.smtp_user, config.smtp_password)
        yield smtp


def error_code(exc):
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "smtp_authentication_failed"
    if isinstance(exc, (ssl.SSLError, smtplib.SMTPNotSupportedError)):
        return "smtp_tls_failed"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "smtp_recipient_rejected"
    return "smtp_connection_or_delivery_failed"


def check_connection(config):
    status = email_status(config)
    if not status["ready"]:
        return {
            "ok": False,
            "code": "smtp_not_configured",
            "missing": status["missing"],
        }
    try:
        with connection(config) as smtp:
            code, _ = smtp.noop()
            if code != 250:
                return {"ok": False, "code": "smtp_noop_failed"}
        return {
            "ok": True,
            "code": "smtp_connected",
            "note": "TLS/authentication checked. No email was sent.",
        }
    except (OSError, smtplib.SMTPException) as exc:
        return {"ok": False, "code": error_code(exc)}


def deliver(config, to, subject, body):
    if not email_status(config)["ready"]:
        return False
    if not mailbox(to) or "\r" in subject or "\n" in subject:
        logging.warning("email_delivery_failed code=invalid_message_headers")
        return False
    msg = EmailMessage()
    msg["From"] = config.smtp_from
    msg["To"] = to
    msg["Reply-To"] = config.support_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    msg["Message-ID"] = make_msgid(domain=mailbox(config.smtp_from).split("@")[1])
    msg.set_content(body)
    try:
        with connection(config) as smtp:
            refused = smtp.send_message(
                msg, from_addr=mailbox(config.smtp_from), to_addrs=[mailbox(to)]
            )
        if refused:
            logging.warning("email_delivery_failed code=smtp_recipient_rejected")
            return False
        return True
    except (OSError, smtplib.SMTPException) as exc:
        # SMTP replies can include addresses and provider details; never log them.
        logging.warning("email_delivery_failed code=%s", error_code(exc))
        return False

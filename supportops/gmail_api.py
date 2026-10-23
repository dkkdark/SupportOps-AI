"""Bounded Google OAuth/Gmail HTTP calls and inert email text extraction."""

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr
from html.parser import HTMLParser
from urllib.parse import urlencode
import httpx
from cryptography.fernet import Fernet, InvalidToken

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALLBACK = "/api/integrations/gmail/callback"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN = "https://oauth2.googleapis.com/token"
ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
EMAIL = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


class GmailError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def ready(config):
    if not (
        config.google_oauth_client_id
        and config.google_oauth_client_secret
        and config.gmail_token_encryption_key
    ):
        return False
    try:
        Fernet(config.gmail_token_encryption_key.encode())
    except (ValueError, TypeError):
        return False
    return config.app_env != "production" or config.public_base_url.startswith(
        "https://"
    )


def seal(config, value, context):
    return (
        Fernet(config.gmail_token_encryption_key.encode())
        .encrypt(json.dumps({"value": value, "context": context}).encode())
        .decode()
    )


def unseal(config, value, context):
    try:
        result = json.loads(
            Fernet(config.gmail_token_encryption_key.encode()).decrypt(value.encode())
        )
        if result["context"] != context or not isinstance(result["value"], str):
            raise ValueError()
        return result["value"]
    except (InvalidToken, ValueError, TypeError, KeyError):
        raise GmailError("gmail_credentials_unavailable") from None


def authorization_url(config, state, verifier):
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": config.google_oauth_client_id,
            "redirect_uri": config.public_base_url.rstrip("/") + CALLBACK,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "access_type": "offline",
            "prompt": "consent select_account",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


def valid_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise GmailError("gmail_invalid_response")
    return value


def history_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,40}", value):
        raise GmailError("gmail_invalid_response")
    return value


class Google:
    def __init__(self, config, transport=None):
        self.config = config
        self.client = httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(10, connect=5),
        )

    def close(self):
        self.client.close()

    def request(self, method, url, access=None, **kwargs):
        headers = {"Authorization": "Bearer " + access} if access else {}
        try:
            with self.client.stream(method, url, headers=headers, **kwargs) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise GmailError("gmail_response_too_large")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError()
                if response.is_success:
                    return result
                error = result.get("error", {})
                if error == "invalid_grant" or response.status_code == 401:
                    raise GmailError("gmail_reconnect_required")
                reasons = (
                    {
                        e.get("reason")
                        for e in error.get("errors", [])
                        if isinstance(e, dict)
                    }
                    if isinstance(error, dict)
                    else set()
                )
                if response.status_code == 429 or reasons & {
                    "rateLimitExceeded",
                    "userRateLimitExceeded",
                    "dailyLimitExceeded",
                }:
                    raise GmailError("gmail_rate_limited")
                if response.status_code == 404:
                    raise GmailError("gmail_not_found")
                if response.status_code == 403:
                    raise GmailError("gmail_access_denied")
                if response.status_code == 400 and kwargs.get("params", {}).get(
                    "pageToken"
                ):
                    raise GmailError("gmail_page_expired")
                raise GmailError("gmail_temporarily_unavailable")
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise GmailError("gmail_temporarily_unavailable") from None

    def exchange(self, code, verifier):
        return self.request(
            "POST",
            TOKEN,
            data={
                "client_id": self.config.google_oauth_client_id,
                "client_secret": self.config.google_oauth_client_secret,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": self.config.public_base_url.rstrip("/") + CALLBACK,
                "grant_type": "authorization_code",
            },
        )

    def refresh(self, token):
        return self.request(
            "POST",
            TOKEN,
            data={
                "client_id": self.config.google_oauth_client_id,
                "client_secret": self.config.google_oauth_client_secret,
                "refresh_token": token,
                "grant_type": "refresh_token",
            },
        )

    def profile(self, access):
        result = self.request("GET", API + "/profile", access)
        mailbox = result.get("emailAddress", "").casefold()
        if len(mailbox) > 160 or not EMAIL.fullmatch(mailbox):
            raise GmailError("gmail_invalid_response")
        return mailbox, history_id(result.get("historyId"))

    def label(self, access, name):
        if name.casefold() == "inbox":
            return "INBOX", "INBOX"
        result = self.request("GET", API + "/labels", access)
        for label in result.get("labels", []):
            if (
                label.get("type") == "user"
                and label.get("name", "").casefold() == name.casefold()
            ):
                return valid_id(label.get("id")), label["name"][:100]
        raise GmailError("gmail_label_not_found")

    def message(self, access, message_id):
        url = API + "/messages/" + valid_id(message_id)
        try:
            return self.request("GET", url, access, params={"format": "full"})
        except GmailError as error:
            if error.code != "gmail_response_too_large":
                raise
            result = self.request(
                "GET",
                url,
                access,
                params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
            )
            result["_body_unavailable"] = True
            return result


def access_token(result, require_scope=False):
    access = result.get("access_token")
    if (
        not isinstance(access, str)
        or not 1 <= len(access) <= 8192
        or result.get("token_type", "").casefold() != "bearer"
    ):
        raise GmailError("gmail_invalid_response")
    if (require_scope or "scope" in result) and SCOPE not in result.get(
        "scope", ""
    ).split():
        raise GmailError("gmail_access_denied")
    return access


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        elif not self.hidden and tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def header(value):
    pieces = []
    for text, charset in decode_header(value[:2000]):
        if isinstance(text, bytes):
            try:
                text = text.decode(charset or "utf-8", errors="replace")
            except LookupError:
                text = text.decode("utf-8", errors="replace")
        pieces.append(text)
    return "".join(pieces).replace("\x00", "").strip()


def email_text(message, mailbox, label_id, since):
    labels = set(message.get("labelIds", []))
    if (
        "INBOX" not in labels
        or label_id not in labels
        or labels & {"SENT", "DRAFT", "SPAM", "TRASH"}
    ):
        return None
    try:
        received = datetime.fromtimestamp(
            int(message["internalDate"]) / 1000, timezone.utc
        )
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        raise GmailError("gmail_invalid_response") from None
    if received < since:
        return None
    payload = message.get("payload", {})
    headers = {
        h.get("name", "").casefold(): h.get("value", "")
        for h in payload.get("headers", [])
    }
    name, sender = parseaddr(header(headers.get("from", "")))
    if sender.casefold() == mailbox:
        return None
    sender = sender[:160] if EMAIL.fullmatch(sender) else ""
    subject = header(headers.get("subject", ""))[:180]
    if len(subject) < 3:
        subject = "Email request" + (": " + subject if subject else "")
    plain, rich, attachments, visited = [], [], False, 0
    stack = [] if message.get("_body_unavailable") else [(payload, 0)]
    while stack:
        part, depth = stack.pop()
        visited += 1
        if depth > 12 or visited > 200:
            raise GmailError("gmail_message_structure_limit")
        if part.get("filename") or part.get("body", {}).get("attachmentId"):
            attachments = True
            continue
        stack.extend((child, depth + 1) for child in reversed(part.get("parts", [])))
        kind = part.get("mimeType", "").casefold()
        encoded = part.get("body", {}).get("data", "")
        if kind not in ("text/plain", "text/html") or not encoded:
            continue
        try:
            raw = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
        except (ValueError, TypeError):
            continue
        part_headers = " ".join(
            h.get("value", "")
            for h in part.get("headers", [])
            if h.get("name", "").casefold() == "content-type"
        )
        match = re.search(r'charset\s*=\s*["\']?([\w-]+)', part_headers, re.I)
        try:
            text = raw.decode(match.group(1) if match else "utf-8", errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        if kind == "text/plain":
            plain.append(text)
        else:
            parser = TextOnly()
            parser.feed(text)
            rich.append("".join(parser.parts))
    body = "\n\n".join(plain or rich).replace("\x00", "").strip()
    notes = []
    if not body or message.get("_body_unavailable"):
        body = "This email has no importable text or exceeds the import size limit. Review the original message in Gmail."
    if attachments:
        notes.append("Attachments remain in Gmail and are not imported.")
    if len(body) > 4800:
        body = body[:4800]
        notes.append("Email shortened. Review the complete message in Gmail.")
    if len(body) < 10:
        body += "\n[Received by email]"
    if notes:
        body += "\n\n[" + " ".join(notes) + "]"
    customer = (name or sender or "Email sender")[:100]
    if len(customer) < 2:
        customer = sender or "Email sender"
    return {
        "created_at": received,
        "customer": customer,
        "email": sender,
        "subject": subject,
        "message": body[:5000],
        "channel": "email",
    }

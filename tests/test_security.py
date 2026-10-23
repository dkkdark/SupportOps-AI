"""Regression tests for resource exhaustion, hostile uploads and inbound token scope."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import BoundedSemaphore, Event, Lock
from zipfile import ZipFile
import pytest
from fastapi import HTTPException
from supportops.passwords import BoundedPasswords, PASSWORDS
from tests.test_workspaces import register, TICKET, PASSWORD


def test_password_work_is_bounded_and_capacity_recovers_after_failure():
    passwords = BoundedPasswords()
    release = Event()
    entered = Event()
    lock = Lock()
    running = 0

    def expensive(*args):
        nonlocal running
        with lock:
            running += 1
            if running == 2:
                entered.set()
        assert release.wait(timeout=10)
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(passwords._run, expensive)
        second = pool.submit(passwords._run, expensive)
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(HTTPException) as busy:
                passwords.hash("Do not allocate another Argon2 buffer")
            assert busy.value.status_code == 429
        finally:
            release.set()
        assert first.result(timeout=10) and second.result(timeout=10)

    def failing():
        raise ValueError("synthetic failure")

    with pytest.raises(ValueError):
        passwords._run(failing)
    assert passwords._run(lambda: True)


def test_password_overload_returns_retryable_errors_and_rolls_back_signup(
    platform, monkeypatch
):
    owner, guest, _ = platform
    register(owner)
    monkeypatch.setattr(PASSWORDS, "_slots", BoundedSemaphore(0))
    response = guest.post(
        "/api/auth/login", json={"email": "alex@acme.test", "password": PASSWORD}
    )
    assert response.status_code == 429 and response.headers["Retry-After"] == "1"
    assert (
        owner.post(
            "/api/account/deletion-request", json={"password": PASSWORD}
        ).status_code
        == 429
    )
    assert (
        guest.post(
            "/api/auth/register",
            json={
                "name": "James Miller",
                "business_name": "New business",
                "email": "new@example.test",
                "password": PASSWORD,
            },
        ).status_code
        == 429
    )
    assert guest.get("/api/config").json()["registration_available"]
    assert owner.get("/api/tickets").status_code == 200


def docx(xml):
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return data.getvalue()


@pytest.mark.parametrize("encoding", ["UTF-8", "UTF-16"])
def test_docx_rejects_entities_in_all_encodings(platform, encoding):
    owner, _, _ = platform
    register(owner)
    xml = (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        '<!DOCTYPE document [<!ENTITY sample "Entity expansion must be rejected">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:p><w:t>&sample;</w:t></w:p></w:document>"
    )
    response = owner.post(
        "/api/knowledge/upload",
        data={"title": "Unsafe document"},
        files={"file": ("policy.docx", docx(xml.encode(encoding)))},
    )
    assert response.status_code == 422
    assert not owner.get("/api/knowledge").json()


def test_inbound_tokens_are_write_only_and_bound_to_one_connection(platform):
    owner, other, sender = platform
    register(owner)
    register(other, "Other business", "other@example.test")
    source = owner.post(
        "/api/intakes", json={"name": "CRM requests", "kind": "api"}
    ).json()
    foreign = other.post(
        "/api/intakes", json={"name": "Other CRM", "kind": "api"}
    ).json()
    headers = {"Authorization": "Bearer " + source["token"]}
    for path in [
        "/api/tickets",
        "/api/knowledge",
        "/api/members",
        "/api/account/export",
        "/api/intakes",
    ]:
        assert sender.get(path, headers=headers).status_code == 401
    assert (
        sender.post(
            "/api/inbound/" + foreign["slug"], json=TICKET, headers=headers
        ).status_code
        == 401
    )
    assert (
        sender.post(
            "/api/inbound/" + source["slug"],
            json={**TICKET, "assigned_to": "foreign-user"},
            headers=headers,
        ).status_code
        == 422
    )
    assert not owner.get("/api/tickets").json() and not other.get("/api/tickets").json()
    response = sender.post(
        "/api/inbound/" + source["slug"],
        json={**TICKET, "external_id": "crm-1"},
        headers=headers,
    )
    assert response.status_code == 202
    item = owner.get("/api/tickets/" + response.json()["reference"]).json()
    assert item["status"] == "new" and item["result"] is None
    assert not other.get("/api/tickets").json()
    assert source["token"] not in owner.get("/api/intakes").text
    assert owner.post("/api/intakes/" + source["id"] + "/revoke").status_code == 200
    assert (
        sender.post(
            "/api/inbound/" + source["slug"], json=TICKET, headers=headers
        ).status_code
        == 404
    )


def test_production_disables_api_docs_and_blocks_embedding_without_proxy(tmp_path):
    from fastapi.testclient import TestClient
    from supportops.config import Settings
    from supportops.main import create_app
    from supportops.db import make_database
    from supportops.migration import migrate

    url = f"sqlite:///{tmp_path}/production.db"
    engine, _ = make_database(url)
    migrate(engine)
    engine.dispose()
    app = create_app(Settings(_env_file=None, database_url=url, app_env="production"))
    with TestClient(app, base_url="https://app.example.test") as client:
        for path in ["/docs", "/redoc", "/openapi.json"]:
            assert client.get(path).status_code == 404
        response = client.get("/")
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert "object-src 'none'" in response.headers["Content-Security-Policy"]
        assert client.get("/api/tickets").status_code == 401

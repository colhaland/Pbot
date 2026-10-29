import socket

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app import crypto, main
from app.settings import Settings


def test_secret_encryption_uses_record_bound_aad(monkeypatch):
    settings = Settings(encryption_master_key=SecretStr("a1" * 32))
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    encrypted = crypto.encrypt_secret("not-a-real-password", table="website_credentials.password", record_id="credential-1")

    assert crypto.decrypt_secret(encrypted, table="website_credentials.password", record_id="credential-1") == "not-a-real-password"
    with pytest.raises(crypto.EncryptionError, match="DECRYPTION_FAILED"):
        crypto.decrypt_secret(encrypted, table="website_credentials.password", record_id="credential-2")
    assert b"not-a-real-password" not in encrypted


def test_http_and_private_target_urls_are_rejected():
    with pytest.raises(HTTPException) as http_error:
        main._validate_target("http://example.test")
    assert http_error.value.detail["code"] == "INVALID_URL"

    with pytest.raises(HTTPException) as ip_error:
        main._validate_target("https://127.0.0.1/admin")
    assert ip_error.value.detail["code"] == "PRIVATE_URL_BLOCKED"


def test_dns_resolution_to_loopback_is_rejected(monkeypatch):
    monkeypatch.setattr(
        main.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.2", 443))],
    )
    with pytest.raises(HTTPException) as error:
        main._validate_target("https://public-looking.example")
    assert error.value.detail["code"] == "PRIVATE_URL_BLOCKED"


def test_schedule_accepts_only_read_actions():
    website = type("TestWebsite", (), {"allowed_actions": ["extract_data"]})()
    data = main.ScheduleInput(name="Harian", website_id="site_01", action="extract_data", payload={}, cron_expr="0 8 * * *", timezone="Asia/Jakarta")
    parameters, next_run = main.validate_schedule(data, website)
    assert parameters == {}
    assert next_run.tzinfo is not None

    write = main.ScheduleInput(name="Write", website_id="site_01", action="fill_form", payload={"template": "profile", "draft_id": "1"}, cron_expr="0 8 * * *", timezone="Asia/Jakarta")
    with pytest.raises(HTTPException) as error:
        main.validate_schedule(write, type("TestWebsite", (), {"allowed_actions": ["fill_form"]})())
    assert error.value.detail["code"] == "SCHEDULE_WRITE_FORBIDDEN"


def test_sensitive_ai_input_is_blocked_before_provider_call():
    from app.ai_gateway import contains_sensitive_text

    assert contains_sensitive_text("password=very-secret")
    assert contains_sensitive_text("AIza012345678901234567890123456789012345")
    assert not contains_sensitive_text("Tolong ambil ringkasan penjualan hari ini")
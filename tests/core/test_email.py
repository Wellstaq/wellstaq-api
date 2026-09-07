from __future__ import annotations

import json

import httpx
import pytest

from app.core import email as email_module


def configure_brevo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        email_module.settings,
        "BREVO_API_URL",
        "https://api.brevo.com/v3/smtp/email",
    )
    monkeypatch.setattr(email_module.settings, "BREVO_API_KEY", "brevo-api-key")
    monkeypatch.setattr(email_module.settings, "BREVO_HTTP_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(email_module.settings, "MAIL_FROM", "info@producthubafrica.org")
    monkeypatch.setattr(email_module.settings, "MAIL_FROM_NAME", "Wellstaq")


def install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> None:
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def make_client(**kwargs):
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(email_module.httpx, "AsyncClient", make_client)


@pytest.mark.asyncio
async def test_sends_transactional_email_through_brevo_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_brevo(monkeypatch)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(201, json={"messageId": "<message-id@brevo>"})

    install_transport(monkeypatch, handler)

    await email_module.send_email(
        "member@example.com",
        "Your verification code",
        "<strong>123456</strong>",
    )

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "POST"
    assert str(request.url) == "https://api.brevo.com/v3/smtp/email"
    assert request.headers["api-key"] == "brevo-api-key"
    assert json.loads(request.content) == {
        "sender": {
            "email": "info@producthubafrica.org",
            "name": "Wellstaq",
        },
        "to": [{"email": "member@example.com"}],
        "subject": "Your verification code",
        "htmlContent": "<strong>123456</strong>",
    }


@pytest.mark.asyncio
async def test_raises_safe_error_for_brevo_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_brevo(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "unauthorized", "message": "bad key"})

    install_transport(monkeypatch, handler)

    with pytest.raises(RuntimeError, match="status 401"):
        await email_module.send_email("member@example.com", "Subject", "<p>Body</p>")


@pytest.mark.asyncio
async def test_rejects_missing_brevo_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_brevo(monkeypatch)
    monkeypatch.setattr(email_module.settings, "BREVO_API_KEY", "")

    with pytest.raises(RuntimeError, match="BREVO_API_KEY"):
        await email_module.send_email("member@example.com", "Subject", "<p>Body</p>")

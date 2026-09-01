from __future__ import annotations

from email.message import EmailMessage

import pytest

from app.core import email as email_module


class FakeSmtp:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls: list[object] = []
        self.message: EmailMessage | None = None

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, *, context: object) -> None:
        self.calls.append(("starttls", context))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", username, password))

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append("send_message")
        self.message = message

    def quit(self) -> None:
        self.calls.append("quit")

    def close(self) -> None:
        self.calls.append("close")


def configure_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_module.settings, "SMTP_HOST", "smtp-relay.brevo.com")
    monkeypatch.setattr(email_module.settings, "SMTP_PORT", 587)
    monkeypatch.setattr(email_module.settings, "SMTP_USERNAME", "smtp-user")
    monkeypatch.setattr(email_module.settings, "SMTP_PASSWORD", "smtp-key")
    monkeypatch.setattr(email_module.settings, "SMTP_USE_TLS", True)
    monkeypatch.setattr(email_module.settings, "SMTP_USE_SSL", False)
    monkeypatch.setattr(email_module.settings, "SMTP_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(email_module.settings, "MAIL_FROM", "info@producthubafrica.org")
    monkeypatch.setattr(email_module.settings, "MAIL_FROM_NAME", "Wellstaq")


def test_sends_html_email_with_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_smtp(monkeypatch)
    clients: list[FakeSmtp] = []

    def make_client(host: str, port: int, timeout: float) -> FakeSmtp:
        client = FakeSmtp(host, port, timeout)
        clients.append(client)
        return client

    monkeypatch.setattr(email_module.smtplib, "SMTP", make_client)

    email_module._send_email_sync(
        "member@example.com",
        "Your verification code",
        "<strong>123456</strong>",
    )

    client = clients[0]
    assert (client.host, client.port, client.timeout) == (
        "smtp-relay.brevo.com",
        587,
        30.0,
    )
    assert client.calls[0] == "ehlo"
    assert client.calls[1][0] == "starttls"
    assert client.calls[2] == "ehlo"
    assert client.calls[3] == ("login", "smtp-user", "smtp-key")
    assert client.calls[-2:] == ["send_message", "quit"]
    assert client.message is not None
    assert client.message["From"] == "Wellstaq <info@producthubafrica.org>"
    assert client.message["To"] == "member@example.com"
    html_part = client.message.get_body(preferencelist=("html",))
    assert html_part is not None
    assert html_part.get_content().strip() == "<strong>123456</strong>"


def test_rejects_missing_smtp_password(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_smtp(monkeypatch)
    monkeypatch.setattr(email_module.settings, "SMTP_PASSWORD", "")

    with pytest.raises(RuntimeError, match="SMTP_PASSWORD"):
        email_module._send_email_sync("member@example.com", "Subject", "<p>Body</p>")

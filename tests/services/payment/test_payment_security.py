import hashlib
import hmac

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.payment.checkout_service import _validate_callback_url
from app.services.payment.webhook_service import verify_paystack_signature


def test_paystack_signature_uses_exact_body(monkeypatch):
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", "test-secret")
    body = b'{"event":"charge.success"}'
    signature = hmac.new(b"test-secret", body, hashlib.sha512).hexdigest()
    assert verify_paystack_signature(payload_body=body, signature_header=signature)
    assert not verify_paystack_signature(payload_body=body + b" ", signature_header=signature)


def test_callback_url_must_use_approved_origin(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_CALLBACK_ALLOWED_ORIGINS", ["https://app.example.com"])
    monkeypatch.setattr(settings, "CORS_ORIGINS", [])
    monkeypatch.setattr(settings, "PUBLIC_APP_ORIGIN", "")
    assert _validate_callback_url("https://app.example.com/payment/complete") == "https://app.example.com/payment/complete"
    with pytest.raises(HTTPException) as exc:
        _validate_callback_url("https://attacker.example/payment/complete")
    assert exc.value.status_code == 400


def test_non_local_http_callback_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_CALLBACK_ALLOWED_ORIGINS", ["http://app.example.com"])
    monkeypatch.setattr(settings, "CORS_ORIGINS", [])
    monkeypatch.setattr(settings, "PUBLIC_APP_ORIGIN", "")
    with pytest.raises(HTTPException) as exc:
        _validate_callback_url("http://app.example.com/payment/complete")
    assert exc.value.status_code == 400


def test_callback_url_accepts_cors_and_public_app_origins(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_CALLBACK_ALLOWED_ORIGINS", ["https://payments.example.com"])
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example.com"])
    monkeypatch.setattr(settings, "PUBLIC_APP_ORIGIN", "https://custom.example.com/")

    assert _validate_callback_url("https://app.example.com/payment/complete")
    assert _validate_callback_url("https://custom.example.com/payment/complete")


def test_deployed_org_app_callback_is_always_trusted(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_CALLBACK_ALLOWED_ORIGINS", [])
    monkeypatch.setattr(settings, "CORS_ORIGINS", [])
    monkeypatch.setattr(settings, "PUBLIC_APP_ORIGIN", "")

    assert _validate_callback_url(
        "https://appwellstaq.vercel.app/dashboard/settings?tab=billing"
    )

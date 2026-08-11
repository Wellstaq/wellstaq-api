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
    assert _validate_callback_url("https://app.example.com/payment/complete") == "https://app.example.com/payment/complete"
    with pytest.raises(HTTPException) as exc:
        _validate_callback_url("https://attacker.example/payment/complete")
    assert exc.value.status_code == 400


def test_non_local_http_callback_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_CALLBACK_ALLOWED_ORIGINS", ["http://app.example.com"])
    with pytest.raises(HTTPException) as exc:
        _validate_callback_url("http://app.example.com/payment/complete")
    assert exc.value.status_code == 400

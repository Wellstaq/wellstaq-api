"""Tests for the common service exception contract."""

from fastapi import status

from app.services.errors import AppError, AuthError, OtpError


def test_app_error_defaults_to_bad_request_and_exposes_safe_message():
    error = AppError("Readable failure")

    assert str(error) == "Readable failure"
    assert error.message == "Readable failure"
    assert error.status_code == status.HTTP_400_BAD_REQUEST


def test_app_error_accepts_specific_http_status():
    error = AppError("Missing", status.HTTP_404_NOT_FOUND)

    assert error.status_code == status.HTTP_404_NOT_FOUND


def test_auth_and_otp_errors_share_the_app_error_contract():
    assert isinstance(AuthError("Denied"), AppError)
    assert isinstance(OtpError("Expired"), AppError)

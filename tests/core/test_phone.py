import pytest

from app.core.phone import normalize_phone_number


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("09122546487", "+2349122546487"),
        ("+2349122546487", "+2349122546487"),
        ("+23409122546487", "+2349122546487"),
        ("+1 202-555-0123", "+12025550123"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_normalize_phone_number_to_e164(value: str, expected: str):
    assert normalize_phone_number(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "12345", "+999123456789", "not-a-phone"],
)
def test_rejects_invalid_phone_numbers(value: str):
    with pytest.raises(ValueError, match="Phone number|required|valid phone number"):
        normalize_phone_number(value)

"""International phone-number parsing and canonical storage."""

import phonenumbers
from phonenumbers import PhoneNumberFormat, NumberParseException


DEFAULT_PHONE_REGION = "NG"


def normalize_phone_number(value: str, default_region: str = DEFAULT_PHONE_REGION) -> str:
    """Validate a phone number and return its canonical E.164 representation.

    Numbers beginning with ``+`` must include a valid international calling code.
    National-format numbers are interpreted using ``default_region`` (Nigeria by
    default), so ``09122546487`` becomes ``+2349122546487``.
    """
    candidate = value.strip()
    if not candidate:
        raise ValueError("Phone number is required.")

    try:
        parsed = phonenumbers.parse(candidate, None if candidate.startswith("+") else default_region)
    except NumberParseException as exc:
        raise ValueError(
            "Enter a valid phone number. Use international format such as +2349122546487, "
            "or a Nigerian national number such as 09122546487."
        ) from exc

    if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
        raise ValueError(
            "Enter a valid phone number for its country. Use international format such as "
            "+2349122546487; Nigerian numbers may also use 09122546487."
        )

    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)

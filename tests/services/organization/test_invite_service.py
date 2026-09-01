from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.organization import invite_service


def test_generate_invite_code_is_zero_padded_to_six_digits(monkeypatch):
    monkeypatch.setattr(invite_service.secrets, "randbelow", lambda limit: 42)

    code = invite_service._generate_invite_code()

    assert code == "000042"
    assert len(code) == 6
    assert code.isdigit()


@pytest.mark.asyncio
async def test_generate_unique_invite_code_retries_a_collision(monkeypatch):
    generated = iter(("123456", "654321"))
    monkeypatch.setattr(invite_service, "_generate_invite_code", lambda: next(generated))

    collision = MagicMock()
    collision.scalar_one_or_none.return_value = "existing-invite-id"
    available = MagicMock()
    available.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute.side_effect = (collision, available)

    code = await invite_service._generate_unique_invite_code(session)

    assert code == "654321"
    assert session.execute.await_count == 2

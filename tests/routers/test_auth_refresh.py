"""
HTTP-level regression test for POST /auth/refresh.

The route previously declared response_model=AccessTokenResponse
(access_token + token_type only), even though refresh_access_token
rotates the refresh token server-side on every call — overwriting the
UserDevice's stored hash immediately. The new raw refresh token was
computed but silently stripped from the HTTP response by FastAPI's
response_model filtering, so a client could never refresh a second
time: its only copy of the refresh token was the now-invalidated one.
Service-level tests (tests/services/auth/test_session_service.py)
already caught this at the function-return level; this test catches it
at the actual wire-response level, where the real bug was.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import authenticate_user, make_org_with_owner


@pytest.mark.asyncio
async def test_refresh_response_includes_rotated_refresh_token(client, db_session: AsyncSession):
    _org, owner = await make_org_with_owner(db_session)
    _headers, tokens = await authenticate_user(db_session, owner)

    response = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["refresh_token"] != tokens["refresh_token"]


@pytest.mark.asyncio
async def test_rotated_refresh_token_can_be_used_for_a_second_refresh(client, db_session: AsyncSession):
    """End-to-end proof the bug is fixed: a client that only ever stores
    whatever /auth/refresh last returned can keep refreshing indefinitely."""
    _org, owner = await make_org_with_owner(db_session)
    _headers, tokens = await authenticate_user(db_session, owner)

    first = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200
    rotated_token = first.json()["refresh_token"]

    second = await client.post("/auth/refresh", json={"refresh_token": rotated_token})
    assert second.status_code == 200
    assert second.json()["refresh_token"] != rotated_token


@pytest.mark.asyncio
async def test_old_refresh_token_rejected_after_rotation(client, db_session: AsyncSession):
    _org, owner = await make_org_with_owner(db_session)
    _headers, tokens = await authenticate_user(db_session, owner)

    await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    replay = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401

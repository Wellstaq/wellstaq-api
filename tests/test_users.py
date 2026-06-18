"""User model and auth endpoint tests."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_returns_hello(client: AsyncClient):
    """Sanity check — root route responds correctly."""
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello from Wellstaq API"}
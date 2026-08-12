"""Best-effort mobile push delivery for registered Expo tokens."""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MobilePushToken

logger = logging.getLogger(__name__)
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def send_mobile_push(*, session: AsyncSession, user_id: str, title: str, body: str, data: dict) -> None:
    tokens = (await session.execute(select(MobilePushToken).where(
        MobilePushToken.user_id == user_id,
        MobilePushToken.is_active.is_(True),
        MobilePushToken.provider == "expo",
    ))).scalars().all()
    if not tokens:
        return
    payload = [{"to": token.token, "title": title, "body": body, "data": data, "sound": "default"} for token in tokens]
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(EXPO_PUSH_URL, json=payload)
            response.raise_for_status()
            results = response.json().get("data", [])
        for token, result in zip(tokens, results):
            if result.get("status") == "error" and result.get("details", {}).get("error") == "DeviceNotRegistered":
                token.is_active = False
        await session.flush()
    except Exception:
        logger.warning("Mobile push delivery failed", exc_info=True, extra={"user_id": user_id})

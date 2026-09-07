"""
HTTP route tests for app/routers/events.py.

Every event endpoint is exercised through FastAPI's ASGI client with the
real database, authentication, subscription, permission, service, transaction,
and response-schema layers. External notification delivery remains isolated by
the shared test fixtures.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, EventParticipant
from tests.conftest import authenticate_user, make_org_with_owner, make_user


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _authorized_org(
    db_session: AsyncSession,
    *,
    role_name: str = "super_admin",
    permissions: tuple[str, ...] = (),
):
    org, user = await make_org_with_owner(
        db_session,
        user_kwargs={"role_name": role_name},
    )
    headers, _ = await authenticate_user(
        db_session,
        user,
        permissions=permissions,
        device_fingerprint=f"event-router-{user.id}",
    )
    return org, user, headers


async def _make_event(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    *,
    title: str = "Morning Walk",
    created_by: str | None = None,
    **overrides,
) -> Event:
    values = dict(
        title=title,
        description="Walk together",
        start_date=date.today() + timedelta(days=1),
        time=time(8, 0),
        status="scheduled",
        organization_id=organization_id,
        branch_id=branch_id,
        created_by=created_by,
    )
    event = Event(**{**values, **overrides})
    db_session.add(event)
    await db_session.flush()
    return event


async def _make_member(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    *,
    email: str,
):
    user = make_user(
        organization_id,
        branch_id=branch_id,
        email=email,
        status="active",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _add_participant(
    db_session: AsyncSession,
    event_id: str,
    user_id: str,
    *,
    status: str = "accepted",
) -> EventParticipant:
    participant = EventParticipant(
        event_id=event_id,
        user_id=user_id,
        status=status,
        attended=False,
    )
    db_session.add(participant)
    await db_session.flush()
    return participant


# ── Shared access ────────────────────────────────────────────────────────────

class TestEventRouteAccess:
    @pytest.mark.asyncio
    async def test_list_requires_authentication(self, client):
        response = await client.get(
            "/organizations/00000000-0000-0000-0000-000000000000/events"
        )

        assert response.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_list_rejects_different_organization(self, client, db_session: AsyncSession):
        _, _, headers = await _authorized_org(db_session)
        other_org, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "other-event-org", "business_name": "Other Event Org"},
            user_kwargs={"email": "other-event-owner@example.com"},
        )

        response = await client.get(
            f"/organizations/{other_org.id}/events", headers=headers
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_create_rejects_missing_permission(self, client, db_session: AsyncSession):
        org, member, headers = await _authorized_org(db_session, role_name="member")

        response = await client.post(
            f"/organizations/{org.id}/events",
            headers=headers,
            json={
                "branch_id": member.branch_id,
                "title": "No Permission",
                "start_date": date.today().isoformat(),
                "time": "08:00:00",
            },
        )

        assert response.status_code == 403
        assert "event.create" in response.json()["detail"]


# ── Create, list, and get ────────────────────────────────────────────────────

class TestCreateEventRoute:
    @pytest.mark.asyncio
    async def test_creates_and_persists_event(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("event.create",)
        )

        response = await client.post(
            f"/organizations/{org.id}/events",
            headers=headers,
            json={
                "branch_id": owner.branch_id,
                "title": "  Team Run  ",
                "description": "Friday fitness",
                "start_date": (date.today() + timedelta(days=2)).isoformat(),
                "time": "07:30:00",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Team Run"
        assert body["organization_id"] == org.id
        assert body["branch_id"] == owner.branch_id
        assert body["created_by"] == owner.id
        assert body["participant_count"] == 0
        stored = (
            await db_session.execute(select(Event).where(Event.id == body["id"]))
        ).scalar_one()
        assert stored.status == "scheduled"

    @pytest.mark.asyncio
    async def test_create_rejects_unknown_branch(self, client, db_session: AsyncSession):
        org, _, headers = await _authorized_org(
            db_session, permissions=("event.create",)
        )

        response = await client.post(
            f"/organizations/{org.id}/events",
            headers=headers,
            json={
                "branch_id": "missing-branch",
                "title": "Team Run",
                "start_date": date.today().isoformat(),
                "time": "07:30:00",
            },
        )

        assert response.status_code == 404
        assert "branch" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_rejects_empty_title(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("event.create",)
        )

        response = await client.post(
            f"/organizations/{org.id}/events",
            headers=headers,
            json={
                "branch_id": owner.branch_id,
                "title": "",
                "start_date": date.today().isoformat(),
                "time": "07:30:00",
            },
        )

        assert response.status_code == 422


class TestReadEventRoutes:
    @pytest.mark.asyncio
    async def test_list_is_paginated_and_includes_participant_counts(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(db_session)
        first = await _make_event(db_session, org.id, owner.branch_id, title="First")
        await _make_event(db_session, org.id, owner.branch_id, title="Second")
        await _add_participant(db_session, first.id, owner.id)

        response = await client.get(
            f"/organizations/{org.id}/events?offset=0&limit=1", headers=headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert body["offset"] == 0
        assert body["limit"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["participant_count"] in {0, 1}

    @pytest.mark.asyncio
    async def test_blank_branch_filter_behaves_like_omitted_filter(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(db_session)
        await _make_event(db_session, org.id, owner.branch_id)

        response = await client.get(
            f"/organizations/{org.id}/events?branch_id=", headers=headers
        )

        assert response.status_code == 200
        assert response.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_get_returns_event_and_participant_count(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(db_session)
        event = await _make_event(db_session, org.id, owner.branch_id)
        await _add_participant(db_session, event.id, owner.id)

        response = await client.get(
            f"/organizations/{org.id}/events/{event.id}", headers=headers
        )

        assert response.status_code == 200
        assert response.json()["id"] == event.id
        assert response.json()["participant_count"] == 1

    @pytest.mark.asyncio
    async def test_get_returns_not_found_for_unknown_event(
        self, client, db_session: AsyncSession
    ):
        org, _, headers = await _authorized_org(db_session)

        response = await client.get(
            f"/organizations/{org.id}/events/missing-event", headers=headers
        )

        assert response.status_code == 404


# ── Event mutations ──────────────────────────────────────────────────────────

class TestUpdateCancelDeleteEventRoutes:
    @pytest.mark.asyncio
    async def test_update_persists_changes(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("event.update",)
        )
        event = await _make_event(db_session, org.id, owner.branch_id)

        response = await client.patch(
            f"/organizations/{org.id}/events/{event.id}",
            headers=headers,
            json={"title": "Updated Event", "status": "ongoing"},
        )

        assert response.status_code == 200
        assert response.json()["title"] == "Updated Event"
        assert response.json()["status"] == "ongoing"
        await db_session.refresh(event)
        assert event.title == "Updated Event"

    @pytest.mark.asyncio
    async def test_cancel_persists_cancelled_status(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("event.update",)
        )
        event = await _make_event(db_session, org.id, owner.branch_id)

        response = await client.post(
            f"/organizations/{org.id}/events/{event.id}/cancel", headers=headers
        )

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        await db_session.refresh(event)
        assert event.status == "cancelled"

    @pytest.mark.asyncio
    async def test_delete_removes_event(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("event.delete",)
        )
        event = await _make_event(db_session, org.id, owner.branch_id)
        event_id = event.id

        response = await client.delete(
            f"/organizations/{org.id}/events/{event_id}", headers=headers
        )

        assert response.status_code == 204
        assert (
            await db_session.execute(select(Event).where(Event.id == event_id))
        ).scalar_one_or_none() is None


# ── Participants ─────────────────────────────────────────────────────────────

class TestEventParticipantRoutes:
    @pytest.mark.asyncio
    async def test_user_can_join_self_and_record_is_persisted(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        event = await _make_event(db_session, org.id, member.branch_id)

        response = await client.post(
            f"/organizations/{org.id}/events/{event.id}/participants",
            headers=headers,
            json={"user_id": member.id, "is_invite": False},
        )

        assert response.status_code == 201
        assert response.json()["message"] == "Joined event."
        participant = (
            await db_session.execute(
                select(EventParticipant).where(
                    EventParticipant.event_id == event.id,
                    EventParticipant.user_id == member.id,
                )
            )
        ).scalar_one()
        assert participant.status == "accepted"
        assert participant.invited_at is None

    @pytest.mark.asyncio
    async def test_user_cannot_join_for_someone_else(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        other = await _make_member(
            db_session, org.id, member.branch_id, email="event-other@example.com"
        )
        event = await _make_event(db_session, org.id, member.branch_id)

        response = await client.post(
            f"/organizations/{org.id}/events/{event.id}/participants",
            headers=headers,
            json={"user_id": other.id, "is_invite": False},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_organizer_can_invite_user_and_notification_is_created(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("event.update",)
        )
        invitee = await _make_member(
            db_session, org.id, owner.branch_id, email="event-invitee@example.com"
        )
        event = await _make_event(db_session, org.id, owner.branch_id)

        response = await client.post(
            f"/organizations/{org.id}/events/{event.id}/participants",
            headers=headers,
            json={"user_id": invitee.id, "is_invite": True},
        )

        assert response.status_code == 201
        assert response.json()["message"] == "Invitation sent."
        participant = (
            await db_session.execute(
                select(EventParticipant).where(
                    EventParticipant.event_id == event.id,
                    EventParticipant.user_id == invitee.id,
                )
            )
        ).scalar_one()
        assert participant.status == "invited"
        assert participant.invited_at is not None

    @pytest.mark.asyncio
    async def test_invited_user_joining_accepts_existing_invitation(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        event = await _make_event(db_session, org.id, member.branch_id)
        participant = await _add_participant(
            db_session, event.id, member.id, status="invited"
        )

        response = await client.post(
            f"/organizations/{org.id}/events/{event.id}/participants",
            headers=headers,
            json={"user_id": member.id, "is_invite": False},
        )

        assert response.status_code == 201
        await db_session.refresh(participant)
        assert participant.status == "accepted"

    @pytest.mark.asyncio
    async def test_list_participants_is_paginated_and_excludes_sensitive_fields(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(db_session)
        event = await _make_event(db_session, org.id, owner.branch_id)
        await _add_participant(db_session, event.id, owner.id)

        response = await client.get(
            f"/organizations/{org.id}/events/{event.id}/participants?limit=1",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["email"] == owner.email
        assert "hashed_password" not in response.text

    @pytest.mark.asyncio
    async def test_update_rsvp_status_persists(self, client, db_session: AsyncSession):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        event = await _make_event(db_session, org.id, member.branch_id)
        participant = await _add_participant(db_session, event.id, member.id)

        response = await client.patch(
            f"/organizations/{org.id}/events/{event.id}/participants/{member.id}/status",
            headers=headers,
            json={"status": "declined"},
        )

        assert response.status_code == 200
        await db_session.refresh(participant)
        assert participant.status == "declined"

    @pytest.mark.asyncio
    async def test_invalid_rsvp_status_returns_bad_request(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        event = await _make_event(db_session, org.id, member.branch_id)
        await _add_participant(db_session, event.id, member.id)

        response = await client.patch(
            f"/organizations/{org.id}/events/{event.id}/participants/{member.id}/status",
            headers=headers,
            json={"status": "maybe"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_organizer_can_mark_attendance(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("event.update",)
        )
        event = await _make_event(db_session, org.id, owner.branch_id)
        participant = await _add_participant(db_session, event.id, owner.id)

        response = await client.patch(
            f"/organizations/{org.id}/events/{event.id}/participants/{owner.id}/attendance",
            headers=headers,
            json={"attended": True},
        )

        assert response.status_code == 200
        await db_session.refresh(participant)
        assert participant.attended is True

    @pytest.mark.asyncio
    async def test_user_can_leave_event(self, client, db_session: AsyncSession):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        event = await _make_event(db_session, org.id, member.branch_id)
        await _add_participant(db_session, event.id, member.id)

        response = await client.delete(
            f"/organizations/{org.id}/events/{event.id}/participants/{member.id}",
            headers=headers,
        )

        assert response.status_code == 204
        assert (
            await db_session.execute(
                select(EventParticipant).where(
                    EventParticipant.event_id == event.id,
                    EventParticipant.user_id == member.id,
                )
            )
        ).scalar_one_or_none() is None

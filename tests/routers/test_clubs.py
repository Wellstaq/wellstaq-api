"""HTTP route tests for app/routers/clubs.py."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Club, ClubMember
from tests.conftest import authenticate_user, make_org_with_owner, make_user


async def _context(db_session: AsyncSession, *, permissions=("club.create",)):
    org, owner = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(
        db_session,
        owner,
        permissions=permissions,
        device_fingerprint=f"club-router-{owner.id}",
    )
    return org, owner, headers


async def _member_context(db_session: AsyncSession, org, branch_id, *, email):
    user = make_user(org.id, branch_id=branch_id, email=email, status="active")
    db_session.add(user)
    await db_session.flush()
    headers, _ = await authenticate_user(
        db_session, user, device_fingerprint=f"club-member-router-{user.id}"
    )
    return user, headers


async def _create_via_route(client, org, owner, headers, *, name="Running Club"):
    response = await client.post(
        f"/organizations/{org.id}/branches/{owner.branch_id}/clubs",
        headers=headers,
        json={
            "name": name,
            "description": "Weekly movement",
            "privacy": "public",
            "category": "fitness",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestClubRoutes:
    @pytest.mark.asyncio
    async def test_create_list_branch_list_get_edit_and_delete(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _context(db_session)
        created = await _create_via_route(client, org, owner, headers)
        club_id = created["id"]
        assert created["leader_id"] == owner.id
        assert created["member_count"] == 1

        org_list = await client.get(f"/organizations/{org.id}/clubs", headers=headers)
        assert org_list.status_code == 200
        assert org_list.json()["total"] == 1
        item = org_list.json()["items"][0]
        assert "is_member" in item, item
        assert item["is_member"] is True

        branch_list = await client.get(
            f"/organizations/{org.id}/branches/{owner.branch_id}/clubs", headers=headers
        )
        assert branch_list.status_code == 200
        assert branch_list.json()["total"] == 1

        fetched = await client.get(
            f"/organizations/{org.id}/clubs/{club_id}", headers=headers
        )
        assert fetched.status_code == 200
        assert fetched.json()["id"] == club_id

        edited = await client.patch(
            f"/organizations/{org.id}/clubs/{club_id}",
            headers=headers,
            json={"name": "Updated Running Club", "privacy": "private"},
        )
        assert edited.status_code == 200
        assert edited.json()["name"] == "Updated Running Club"

        deleted = await client.delete(
            f"/organizations/{org.id}/clubs/{club_id}", headers=headers
        )
        assert deleted.status_code == 200
        assert (await db_session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_join_list_leave_and_leader_remove_member(
        self, client, db_session: AsyncSession
    ):
        org, owner, owner_headers = await _context(db_session)
        club_id = (await _create_via_route(client, org, owner, owner_headers))["id"]
        member, member_headers = await _member_context(
            db_session, org, owner.branch_id, email="club-joiner@example.com"
        )

        joined = await client.put(
            f"/organizations/{org.id}/clubs/{club_id}/members/{member.id}",
            headers=member_headers,
        )
        assert joined.status_code == 200

        listed = await client.get(
            f"/organizations/{org.id}/clubs/{club_id}/members", headers=owner_headers
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 2

        left = await client.delete(
            f"/organizations/{org.id}/clubs/{club_id}/members/{member.id}/leave",
            headers=member_headers,
        )
        assert left.status_code == 200

        rejoined = await client.put(
            f"/organizations/{org.id}/clubs/{club_id}/members/{member.id}",
            headers=member_headers,
        )
        assert rejoined.status_code == 200
        removed = await client.delete(
            f"/organizations/{org.id}/clubs/{club_id}/members/{member.id}",
            headers=owner_headers,
        )
        assert removed.status_code == 200
        assert (
            await db_session.execute(
                select(ClubMember).where(ClubMember.club_id == club_id, ClubMember.user_id == member.id)
            )
        ).scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_member_without_permission_cannot_create_club(
        self, client, db_session: AsyncSession
    ):
        org, owner, _ = await _context(db_session)
        member, headers = await _member_context(
            db_session, org, owner.branch_id, email="club-no-create@example.com"
        )
        other_branch = Branch(organization_id=org.id, name="Other Branch")
        db_session.add(other_branch)
        await db_session.flush()

        response = await client.post(
            f"/organizations/{org.id}/branches/{other_branch.id}/clubs",
            headers=headers,
            json={"name": "Denied", "privacy": "public", "category": "fitness"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_create_rejects_invalid_category(self, client, db_session: AsyncSession):
        org, owner, headers = await _context(db_session)

        response = await client.post(
            f"/organizations/{org.id}/branches/{owner.branch_id}/clubs",
            headers=headers,
            json={"name": "Invalid", "privacy": "public", "category": "gaming"},
        )

        assert response.status_code == 422

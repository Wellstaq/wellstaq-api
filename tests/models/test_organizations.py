"""
Organization and OrganizationInvite model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from app.models import Organization, OrganizationInvite, User


# ── Factories ──────────────────────────────────────────────────────────────────

def make_user(email: str = "owner@example.com", **overrides) -> User:
    defaults = dict(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        role_name="overall_manager",
        hashed_password="$2b$12$fakehash",
    )
    return User(**{**defaults, **overrides})


def make_org(owner_id: str, **overrides) -> Organization:
    defaults = dict(
        business_name="Acme Corp",
        slug="acme-corp",
        owner_id=owner_id,
    )
    return Organization(**{**defaults, **overrides})


def make_invite(
    organization_id: str,
    invited_by_id: str,
    email: str = "guest@example.com",
    **overrides,
) -> OrganizationInvite:
    defaults = dict(
        organization_id=organization_id,
        invited_email=email,
        invite_code="some-secure-token-123",
        status="pending",
        invited_by=invited_by_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    return OrganizationInvite(**{**defaults, **overrides})


# ── Organization model ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_persists(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

    result = await db_session.execute(select(Organization).where(Organization.id == org.id))
    fetched = result.scalar_one()
    assert fetched.business_name == "Acme Corp"
    assert fetched.slug == "acme-corp"
    assert fetched.owner_id == user.id


@pytest.mark.asyncio
async def test_organization_id_is_uuid_format(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

    parts = org.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_organization_timestamps_auto_set(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

    assert org.created_at is not None
    assert org.updated_at is not None


@pytest.mark.asyncio
async def test_organization_slug_unique(db_session: AsyncSession):
    """Two orgs cannot share the same slug."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_org(user.id, slug="same-slug"))
    await db_session.flush()

    user2 = make_user(email="other@example.com")
    db_session.add(user2)
    await db_session.flush()

    db_session.add(make_org(user2.id, slug="same-slug", business_name="Other Corp"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_optional_fields_nullable(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

    assert org.business_website is None
    assert org.employee_count is None
    assert org.organization_type is None
    assert org.work_model is None


@pytest.mark.asyncio
async def test_organization_full_fields(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(
        user.id,
        business_website="https://acme.com",
        employee_count=250,
        organization_type="enterprise",
        work_model="hybrid",
    )
    db_session.add(org)
    await db_session.flush()

    result = await db_session.execute(select(Organization).where(Organization.id == org.id))
    fetched = result.scalar_one()
    assert fetched.business_website == "https://acme.com"
    assert fetched.employee_count == 250
    assert fetched.organization_type == "enterprise"
    assert fetched.work_model == "hybrid"


@pytest.mark.asyncio
@pytest.mark.parametrize("org_type", ["startup", "enterprise", "ngo", "government", "custom_type"])
async def test_organization_accepts_any_org_type(db_session: AsyncSession, org_type: str):
    """organization_type is a free string — no enum constraint."""
    user = make_user(email=f"{org_type}@example.com")
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id, slug=f"org-{org_type}", organization_type=org_type)
    db_session.add(org)
    await db_session.flush()
    assert org.organization_type == org_type


@pytest.mark.asyncio
@pytest.mark.parametrize("work_model", ["remote", "hybrid", "onsite"])
async def test_organization_accepts_any_work_model(db_session: AsyncSession, work_model: str):
    """work_model is a free string — no enum constraint."""
    user = make_user(email=f"{work_model}@example.com")
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id, slug=f"org-{work_model}", work_model=work_model)
    db_session.add(org)
    await db_session.flush()
    assert org.work_model == work_model


@pytest.mark.asyncio
async def test_organization_owner_fk_must_exist(db_session: AsyncSession):
    """owner_id must reference a real user."""
    org = make_org(owner_id="00000000-0000-0000-0000-000000000000")
    db_session.add(org)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_owner_cannot_be_deleted(db_session: AsyncSession):
    """ondelete=RESTRICT — cannot delete a user who owns an org."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("DELETE FROM users WHERE id = :id"),
            {"id": user.id}
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_one_user_can_own_multiple_orgs(db_session: AsyncSession):
    """No constraint prevents one user from owning multiple organizations."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_org(user.id, slug="org-one", business_name="Org One"))
    db_session.add(make_org(user.id, slug="org-two", business_name="Org Two"))
    await db_session.flush()

    result = await db_session.execute(
        select(Organization).where(Organization.owner_id == user.id)
    )
    orgs = result.scalars().all()
    assert len(orgs) == 2


# ── Organization Relationships ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_owner_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()
    await db_session.refresh(org, ["owner"])

    assert org.owner is not None
    assert org.owner.id == user.id
    assert org.owner.email == "owner@example.com"


@pytest.mark.asyncio
async def test_user_owned_organizations_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_org(user.id, slug="org-a", business_name="Org A"))
    db_session.add(make_org(user.id, slug="org-b", business_name="Org B"))
    await db_session.flush()
    await db_session.refresh(user, ["owned_organizations"])

    assert len(user.owned_organizations) == 2
    slugs = {o.slug for o in user.owned_organizations}
    assert slugs == {"org-a", "org-b"}


# ── OrganizationInvite model ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_invite_persists(db_session: AsyncSession):
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    invite = make_invite(org.id, manager.id, email="newhire@example.com")
    db_session.add(invite)
    await db_session.flush()

    result = await db_session.execute(
        select(OrganizationInvite).where(OrganizationInvite.id == invite.id)
    )
    fetched = result.scalar_one()
    assert fetched.invited_email == "newhire@example.com"  # fixed: was fetched.email
    assert fetched.status == "pending"
    assert fetched.organization_id == org.id
    assert fetched.invited_by == manager.id


@pytest.mark.asyncio
async def test_invite_id_is_uuid_format(db_session: AsyncSession):
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()

    parts = invite.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_invite_timestamps_auto_set(db_session: AsyncSession):
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()

    assert invite.created_at is not None
    assert invite.updated_at is not None
    assert invite.expires_at is not None


@pytest.mark.asyncio
async def test_invite_default_status_is_pending(db_session: AsyncSession):
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()

    assert invite.status == "pending"


@pytest.mark.asyncio
async def test_invite_optional_fields_nullable(db_session: AsyncSession):
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()

    assert invite.branch_id is None
    assert invite.accepted_at is None
    assert invite.accepted_by_id is None


@pytest.mark.asyncio
async def test_invite_code_unique(db_session: AsyncSession):
    """Two invites cannot share the same invite_code."""
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    db_session.add(make_invite(org.id, manager.id, invite_code="same-code"))
    await db_session.flush()

    db_session.add(make_invite(
        org.id, manager.id,
        email="other@example.com",
        invite_code="same-code",
    ))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_invite_org_fk_must_exist(db_session: AsyncSession):
    """organization_id must reference a real organization."""
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    invite = make_invite(
        organization_id="00000000-0000-0000-0000-000000000000",
        invited_by_id=manager.id,
    )
    db_session.add(invite)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_cascades_to_invites(db_session: AsyncSession):
    """Deleting an organization cascades to its invites."""
    manager = make_user(email="boss@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(OrganizationInvite).where(OrganizationInvite.id == invite.id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_invite_accepted_by_nullable_on_user_delete(db_session: AsyncSession):
    """ondelete=SET NULL on accepted_by_id — invite row survives user deletion."""
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    acceptor = make_user(email="acceptor@example.com")
    db_session.add(acceptor)
    await db_session.flush()

    invite = make_invite(org.id, manager.id, accepted_by_id=acceptor.id)
    db_session.add(invite)
    await db_session.flush()

    await db_session.execute(
        text("DELETE FROM users WHERE id = :id"),
        {"id": acceptor.id}
    )
    await db_session.flush()
    await db_session.refresh(invite)

    assert invite.accepted_by_id is None  # nulled out
    assert invite.id is not None          # row still exists


# ── OrganizationInvite Relationships ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_invite_dual_user_relationships(db_session: AsyncSession):
    """A manager sends an invite; a new user accepts it."""
    manager = make_user(email="sender@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    invite = make_invite(org.id, manager.id, email="receiver@example.com")
    db_session.add(invite)
    await db_session.flush()

    new_user = make_user(email="receiver@example.com")
    db_session.add(new_user)
    await db_session.flush()

    invite.status = "accepted"
    invite.accepted_by_id = new_user.id
    await db_session.flush()

    await db_session.refresh(manager, ["sent_invites"])
    await db_session.refresh(new_user, ["joined_via_invite"])
    await db_session.refresh(invite, ["inviter", "accepted_by_user", "organization"])

    assert invite.inviter.id == manager.id
    assert invite.accepted_by_user.id == new_user.id
    assert invite.organization.id == org.id
    assert len(manager.sent_invites) == 1
    assert manager.sent_invites[0].id == invite.id
    assert new_user.joined_via_invite is not None
    assert new_user.joined_via_invite.id == invite.id


@pytest.mark.asyncio
async def test_invite_organization_relationship(db_session: AsyncSession):
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()
    await db_session.refresh(invite, ["organization"])

    assert invite.organization is not None
    assert invite.organization.id == org.id


@pytest.mark.asyncio
async def test_invite_inviter_relationship(db_session: AsyncSession):
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()
    await db_session.refresh(invite, ["inviter"])

    assert invite.inviter is not None
    assert invite.inviter.id == manager.id


@pytest.mark.asyncio
async def test_organization_invites_relationship(db_session: AsyncSession):
    """org.invites loads all invite rows for that org."""
    manager = make_user(email="manager@example.com")
    db_session.add(manager)
    await db_session.flush()

    org = make_org(manager.id)
    db_session.add(org)
    await db_session.flush()

    db_session.add(make_invite(org.id, manager.id, email="a@example.com", invite_code="code-a"))
    db_session.add(make_invite(org.id, manager.id, email="b@example.com", invite_code="code-b"))
    await db_session.flush()
    await db_session.refresh(org, ["invites"])

    assert len(org.invites) == 2
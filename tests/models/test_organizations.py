"""
Organization and OrganizationInvite model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Schema change this file reflects:
- User.organization_id is now NOT NULL with ondelete="CASCADE".
- Organization.owner_id is ondelete="CASCADE" (was RESTRICT) to avoid
  a deadlock against the members cascade during org deletion.
- Because Organization.owner_id and User.organization_id now form a
  circular NOT NULL-ish dependency, every org+owner pair in these
  tests is created via make_org_with_owner(), which mirrors the real
  signup transaction: create org with owner_id deferred -> flush to
  get org.id -> create user with that organization_id -> flush ->
  patch org.owner_id -> flush. No test should construct a User without
  an organization_id, and no test should construct an Organization
  with a permanently-null owner_id outside of that sequence.
- User.role_name (a free string) has been replaced by User.role_id, a
  FK to roles.id (see Permission/Role model). Role is no longer a
  string on the user — it's a real row. Tests that need a user to
  have a role now go through make_user_with_role() (async — it
  resolves/creates a system Role row via get_or_create_system_role()
  and sets role_id), not a bare role_name= kwarg on User(...) directly.
  make_org_with_owner() already does this internally for the owner;
  only call-sites that build non-owner Users need to call it
  themselves.

Known schema limitation acknowledged in tests:
- test_committed_org_owner_id_cannot_be_null documents that the DB
  does NOT currently enforce "committed org must have owner_id". The
  nullable=True on owner_id is required for the circular-FK bootstrap
  but means a raw INSERT with owner_id=NULL will succeed at the DB
  level. This is an application-layer guarantee only. The test is
  marked xfail to document intent vs. current reality; it should be
  converted to a passing test once a deferred NOT NULL constraint or
  equivalent is added.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from app.models import Organization, OrganizationInvite, Role, User
from tests.conftest import (
    get_or_create_system_role,
    make_org_with_owner,
    make_user,
    make_user_with_role,
)


# ── Factories ──────────────────────────────────────────────────────────────────
#
# make_user(), make_user_with_role(), get_or_create_system_role(), and
# make_org_with_owner() now live in conftest.py — shared across every
# test file that needs a User/Organization/Role, so the circular-FK
# resolution and role_id resolution (see conftest.py docstring) are each
# fixed in exactly one place instead of being copy-pasted per file. Only
# factories specific to this file's domain (org membership, invites)
# stay local.

async def make_member(db_session: AsyncSession, organization_id: str, **overrides) -> User:
    """A non-owner member of an existing org — analogous to a user who
    joined via accepted invite. organization_id must already exist.

    role_name (if not overridden) defaults to "employee" and is
    resolved to a real system Role's id via make_user_with_role() —
    role_name is no longer a column on User itself."""
    overrides.setdefault("email", "member@example.com")
    overrides.setdefault("role_name", "employee")
    user = await make_user_with_role(db_session, organization_id=organization_id, **overrides)
    db_session.add(user)
    await db_session.flush()
    return user


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
    org, user = await make_org_with_owner(db_session)

    result = await db_session.execute(select(Organization).where(Organization.id == org.id))
    fetched = result.scalar_one()
    assert fetched.business_name == "Acme Corp"
    assert fetched.slug == "acme-corp"
    assert fetched.owner_id == user.id


@pytest.mark.asyncio
async def test_organization_id_is_uuid_format(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    parts = org.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_organization_timestamps_auto_set(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    assert org.created_at is not None
    assert org.updated_at is not None


@pytest.mark.asyncio
async def test_organization_slug_unique(db_session: AsyncSession):
    """Two orgs cannot share the same slug. The unique constraint fires
    the moment the second Organization row is flushed — before any
    owner User is even created for it, since slug uniqueness is a
    property of Organization alone."""
    await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug="same-slug"),
        user_kwargs=dict(email="owner1@example.com"),
    )

    org2 = Organization(business_name="Other Corp", slug="same-slug", owner_id=None)
    db_session.add(org2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_optional_fields_nullable(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    assert org.business_website is None
    assert org.employee_count is None
    assert org.organization_type is None
    assert org.work_model is None


@pytest.mark.asyncio
async def test_organization_full_fields(db_session: AsyncSession):
    org, _ = await make_org_with_owner(
        db_session,
        org_kwargs=dict(
            business_website="https://acme.com",
            employee_count=250,
            organization_type="enterprise",
            work_model="hybrid",
        ),
    )

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
    org, _ = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=f"org-{org_type}", organization_type=org_type),
        user_kwargs=dict(email=f"{org_type}@example.com"),
    )
    assert org.organization_type == org_type


@pytest.mark.asyncio
@pytest.mark.parametrize("work_model", ["remote", "hybrid", "onsite"])
async def test_organization_accepts_any_work_model(db_session: AsyncSession, work_model: str):
    """work_model is a free string — no enum constraint."""
    org, _ = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=f"org-{work_model}", work_model=work_model),
        user_kwargs=dict(email=f"{work_model}@example.com"),
    )
    assert org.work_model == work_model


@pytest.mark.asyncio
async def test_organization_owner_fk_must_exist(db_session: AsyncSession):
    """owner_id, once set, must reference a real user."""
    org = Organization(
        business_name="Acme Corp",
        slug="acme-corp",
        owner_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(org)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "Schema limitation: owner_id is nullable=True at the DB level to allow the "
        "circular-FK bootstrap transaction (see Organization model docstring). The DB "
        "therefore accepts a committed org with owner_id=NULL even though the application "
        "contract forbids it. This test documents the gap between documented intent and "
        "current schema enforcement. To make this test pass, add a DEFERRABLE INITIALLY "
        "DEFERRED NOT NULL constraint on owner_id (PostgreSQL only) and remove xfail."
    ),
    strict=True,
)
async def test_committed_org_owner_id_cannot_be_null(db_session: AsyncSession):
    """Documents that a committed org with owner_id=NULL is currently NOT
    prevented by the schema — only by application discipline.

    This test is marked xfail(strict=True): it is expected to fail
    today because the DB allows the INSERT. When the schema is
    hardened (deferred constraint or equivalent), this test should
    start passing and xfail should be removed."""
    org = Organization(
        business_name="Ownerless Corp",
        slug="ownerless-corp",
        owner_id=None,
    )
    db_session.add(org)
    await db_session.flush()

    # If we reach commit, the DB accepted a permanently ownerless org —
    # which violates the documented invariant. The xfail captures this.
    await db_session.commit()

    # Assert the invariant we WANT to hold (currently unenforced):
    result = await db_session.execute(
        select(Organization).where(Organization.id == org.id)
    )
    fetched = result.scalar_one_or_none()
    assert fetched is None, (
        "A committed org with owner_id=NULL should not exist — "
        "schema does not enforce this yet (see xfail reason)."
    )


@pytest.mark.asyncio
async def test_make_org_with_owner_signup_invariant(db_session: AsyncSession):
    """The make_org_with_owner() helper (and the real signup transaction
    it mirrors) must leave the database in a fully linked state:
    - org.owner_id == owner.id
    - owner.organization_id == org.id
    - org.owner.organization_id == org.id  (via relationship)

    This is the most critical invariant in the auth/onboarding flow.
    If make_org_with_owner() is ever changed, this test will catch any
    regression that breaks the circular-FK contract."""
    org, owner = await make_org_with_owner(db_session)

    # Both FK directions must be fully resolved after the helper returns
    assert org.owner_id == owner.id, (
        "org.owner_id must point to the owner after make_org_with_owner()"
    )
    assert owner.organization_id == org.id, (
        "owner.organization_id must point to the org after make_org_with_owner()"
    )

    # Verify via relationship traversal (not just raw FK columns)
    await db_session.refresh(org, ["owner"])
    assert org.owner is not None
    assert org.owner.id == owner.id
    assert org.owner.organization_id == org.id, (
        "org.owner.organization_id must equal org.id — "
        "the owner must belong to the org they own"
    )

@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "SQLite does not reliably fire ondelete=CASCADE on deferred/use_alter FKs "
        "(owner_id uses use_alter=True for circular FK resolution). In SQLite the "
        "owner row is deleted but the org survives with owner_id=None instead of "
        "being cascade-deleted. This cascade chain DOES fire correctly in PostgreSQL "
        "(production). The test documents the intended DB behavior and the danger of "
        "deleting an owner directly — it should pass once CI runs against Postgres."
    ),
    strict=True,
)
async def test_deleting_owner_directly_cascades_entire_org(db_session: AsyncSession):
    """Documents the intended DB behavior when the owner User row is deleted
    directly (outside of org deletion).

    Because owner_id is ondelete="CASCADE", deleting the owner row
    should cascade-delete the Organization, which in turn cascade-deletes
    all remaining member Users via User.organization_id ondelete="CASCADE".

    NOTE: This cascade does not fire in SQLite (test DB) because owner_id
    uses use_alter=True for circular-FK bootstrap — SQLite treats it as
    SET NULL instead of CASCADE. The behavior is correct in PostgreSQL.
    The test is marked xfail(strict=True) to document the gap, consistent
    with test_committed_org_owner_id_cannot_be_null above.

    This test exists so future developers understand the blast radius
    if the service-layer guard against direct owner deletion is ever missing."""
    org, owner = await make_org_with_owner(db_session)
    member = await make_member(db_session, org.id, email="bystander@example.com")

    org_id = org.id
    owner_id = owner.id
    member_id = member.id

    await db_session.delete(owner)
    await db_session.flush()
    db_session.expire_all()

    org_result = await db_session.execute(
        select(Organization).where(Organization.id == org_id)
    )
    assert org_result.scalar_one_or_none() is None, (
        "Org must be deleted when its owner is deleted directly "
        "(owner_id ondelete=CASCADE -> org deleted -> members cascade). "
        "This assertion fails in SQLite — see xfail reason."
    )

    member_result = await db_session.execute(
        select(User).where(User.id == member_id)
    )
    assert member_result.scalar_one_or_none() is None

    owner_result = await db_session.execute(
        select(User).where(User.id == owner_id)
    )
    assert owner_result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_user_organization_id_required(db_session: AsyncSession):
    """organization_id is NOT NULL — a User cannot be created without
    a resolved organization (see User model docstring: every User row
    is created either via accepted invite or the atomic org+owner
    signup transaction, never standalone).

    role_id (not role_name) is resolved via get_or_create_system_role()
    first, since role is now a real FK rather than a free string —
    this test is about organization_id, not role, so the role lookup
    just needs to succeed, not be exercised."""
    role = await get_or_create_system_role(db_session, "employee")
    user = User(
        first_name="No",
        last_name="Org",
        email="noorg@example.com",
        role_id=role.id,
        hashed_password="$2b$12$fakehash",
        organization_id=None,
    )
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_deletion_cascades_to_owner(db_session: AsyncSession):
    """Deleting an org (app-owner-only action) deletes the owner's User
    row too — owner_id is ondelete="CASCADE", not RESTRICT, specifically
    so this cascade can complete without the FK graph deadlocking
    against itself. 'Owner cannot be deleted independently of their
    org' is enforced at the app/service layer, not by this constraint."""
    org, owner = await make_org_with_owner(db_session)
    owner_id = owner.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id == owner_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_organization_deletion_cascades_to_all_members(db_session: AsyncSession):
    """Deleting an org deletes every member User row, not just the
    owner — User.organization_id is ondelete="CASCADE" and the
    Organization.members relationship carries cascade="all,
    delete-orphan" to match. No member should ever be orphaned with a
    NULL organization_id after org deletion."""
    org, owner = await make_org_with_owner(db_session)
    member1 = await make_member(db_session, org.id, email="member1@example.com")
    member2 = await make_member(db_session, org.id, email="member2@example.com")

    member_ids = [owner.id, member1.id, member2.id]

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id.in_(member_ids)))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_non_owner_member_does_not_delete_org(db_session: AsyncSession):
    """Deleting a regular member is a normal, unrestricted operation —
    it must not affect the organization or other members."""
    org, owner = await make_org_with_owner(db_session)
    member = await make_member(db_session, org.id, email="leaving@example.com")

    await db_session.delete(member)
    await db_session.flush()

    org_result = await db_session.execute(select(Organization).where(Organization.id == org.id))
    assert org_result.scalar_one_or_none() is not None

    owner_result = await db_session.execute(select(User).where(User.id == owner.id))
    assert owner_result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_one_user_can_own_multiple_orgs(db_session: AsyncSession):
    """No constraint prevents one user from owning multiple
    organizations — owner_id has no uniqueness constraint. Note: the
    user's own User.organization_id still points to exactly one org
    (their 'home' org); owned_organizations is a separate concept from
    membership."""
    org1, owner = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug="org-one", business_name="Org One"),
    )

    org2 = Organization(business_name="Org Two", slug="org-two", owner_id=owner.id)
    db_session.add(org2)
    await db_session.flush()

    result = await db_session.execute(
        select(Organization).where(Organization.owner_id == owner.id)
    )
    orgs = result.scalars().all()
    assert len(orgs) == 2


# ── Organization deactivation ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_is_active_by_default(db_session: AsyncSession):
    org, _ = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug="org-active-default"),
        user_kwargs=dict(email="active_default@example.com"),
    )
    assert org.is_active is True
    assert org.deactivated_at is None


@pytest.mark.asyncio
async def test_organization_can_be_deactivated(db_session: AsyncSession):
    """Deactivation (e.g. non-payment, or owner self-initiated) flips
    is_active and stamps deactivated_at — no rows are deleted, no
    cascades fire. This is the only path available to org_owner;
    hard deletion is app-owner-only."""
    org, _ = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug="org-deactivate"),
        user_kwargs=dict(email="deactivate@example.com"),
    )

    org.is_active = False
    org.deactivated_at = datetime.now(timezone.utc)
    await db_session.flush()

    result = await db_session.execute(select(Organization).where(Organization.id == org.id))
    fetched = result.scalar_one()
    assert fetched.is_active is False
    assert fetched.deactivated_at is not None


@pytest.mark.asyncio
async def test_organization_can_be_reactivated(db_session: AsyncSession):
    """Reactivation (e.g. payment resumes) flips is_active back and
    clears deactivated_at."""
    org, _ = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug="org-reactivate"),
        user_kwargs=dict(email="reactivate@example.com"),
    )

    org.is_active = False
    org.deactivated_at = datetime.now(timezone.utc)
    await db_session.flush()

    org.is_active = True
    org.deactivated_at = None
    await db_session.flush()

    result = await db_session.execute(select(Organization).where(Organization.id == org.id))
    fetched = result.scalar_one()
    assert fetched.is_active is True
    assert fetched.deactivated_at is None


@pytest.mark.asyncio
async def test_organization_deactivation_does_not_delete_children(db_session: AsyncSession):
    """Deactivating an org is reversible — its related rows (invites,
    and crucially its members) survive untouched, unlike hard
    deletion."""
    org, manager = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug="org-deactivate-children"),
        user_kwargs=dict(email="deactivate_children@example.com"),
    )
    member = await make_member(db_session, org.id, email="stays@example.com")

    invite = make_invite(org.id, manager.id, email="stillhere@example.com")
    db_session.add(invite)
    await db_session.flush()

    org.is_active = False
    org.deactivated_at = datetime.now(timezone.utc)
    await db_session.flush()

    invite_result = await db_session.execute(
        select(OrganizationInvite).where(OrganizationInvite.id == invite.id)
    )
    assert invite_result.scalar_one_or_none() is not None

    member_result = await db_session.execute(select(User).where(User.id == member.id))
    assert member_result.scalar_one_or_none() is not None


# ── Organization Relationships ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_owner_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)
    await db_session.refresh(org, ["owner"])

    assert org.owner is not None
    assert org.owner.id == owner.id
    assert org.owner.email == "owner@example.com"


@pytest.mark.asyncio
async def test_user_owned_organizations_relationship(db_session: AsyncSession):
    org1, owner = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug="org-a", business_name="Org A"),
    )
    org2 = Organization(business_name="Org B", slug="org-b", owner_id=owner.id)
    db_session.add(org2)
    await db_session.flush()

    await db_session.refresh(owner, ["owned_organizations"])

    assert len(owner.owned_organizations) == 2
    slugs = {o.slug for o in owner.owned_organizations}
    assert slugs == {"org-a", "org-b"}


@pytest.mark.asyncio
async def test_organization_members_relationship(db_session: AsyncSession):
    """org.members loads owner + every other user attached to the org."""
    org, owner = await make_org_with_owner(db_session)
    member1 = await make_member(db_session, org.id, email="m1@example.com")
    member2 = await make_member(db_session, org.id, email="m2@example.com")

    await db_session.refresh(org, ["members"])

    member_ids = {m.id for m in org.members}
    assert member_ids == {owner.id, member1.id, member2.id}


# ── OrganizationInvite model ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_invite_persists(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

    invite = make_invite(org.id, manager.id, email="newhire@example.com")
    db_session.add(invite)
    await db_session.flush()

    result = await db_session.execute(
        select(OrganizationInvite).where(OrganizationInvite.id == invite.id)
    )
    fetched = result.scalar_one()
    assert fetched.invited_email == "newhire@example.com"
    assert fetched.status == "pending"
    assert fetched.organization_id == org.id
    assert fetched.invited_by == manager.id


@pytest.mark.asyncio
async def test_invite_id_is_uuid_format(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()

    parts = invite.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_invite_timestamps_auto_set(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()

    assert invite.created_at is not None
    assert invite.updated_at is not None
    assert invite.expires_at is not None


@pytest.mark.asyncio
async def test_invite_default_status_is_pending(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()

    assert invite.status == "pending"


@pytest.mark.asyncio
async def test_invite_optional_fields_nullable(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()

    assert invite.branch_id is None
    assert invite.accepted_at is None
    assert invite.accepted_by_id is None


@pytest.mark.asyncio
async def test_invite_code_unique(db_session: AsyncSession):
    """Two invites cannot share the same invite_code."""
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

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
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

    invite = make_invite(
        organization_id="00000000-0000-0000-0000-000000000000",
        invited_by_id=manager.id,
    )
    db_session.add(invite)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_cascades_to_invites(db_session: AsyncSession):
    """Deleting an organization cascades to its invites (alongside
    cascading to all member users — see
    test_organization_deletion_cascades_to_all_members)."""
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="boss@example.com")
    )

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
    """ondelete=SET NULL on accepted_by_id — invite row survives a
    non-owner member's deletion. (Owner deletion is a different path —
    see test_organization_deletion_cascades_to_owner — since an owner
    is never deleted independently of their org.)"""
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )
    acceptor = await make_member(db_session, org.id, email="acceptor@example.com")

    invite = make_invite(org.id, manager.id, accepted_by_id=acceptor.id)
    db_session.add(invite)
    await db_session.flush()

    await db_session.delete(acceptor)
    await db_session.flush()
    await db_session.refresh(invite)

    assert invite.accepted_by_id is None  # nulled out
    assert invite.id is not None          # row still exists


# ── OrganizationInvite Relationships ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_invite_dual_user_relationships(db_session: AsyncSession):
    """A manager sends an invite; a new user accepts it and becomes a
    member of the same org the invite belongs to."""
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="sender@example.com")
    )

    invite = make_invite(org.id, manager.id, email="receiver@example.com")
    db_session.add(invite)
    await db_session.flush()

    new_user = await make_member(db_session, org.id, email="receiver@example.com")

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
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()
    await db_session.refresh(invite, ["organization"])

    assert invite.organization is not None
    assert invite.organization.id == org.id


@pytest.mark.asyncio
async def test_invite_inviter_relationship(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

    invite = make_invite(org.id, manager.id)
    db_session.add(invite)
    await db_session.flush()
    await db_session.refresh(invite, ["inviter"])

    assert invite.inviter is not None
    assert invite.inviter.id == manager.id


@pytest.mark.asyncio
async def test_organization_invites_relationship(db_session: AsyncSession):
    """org.invites loads all invite rows for that org."""
    org, manager = await make_org_with_owner(
        db_session, user_kwargs=dict(email="manager@example.com")
    )

    db_session.add(make_invite(org.id, manager.id, email="a@example.com", invite_code="code-a"))
    db_session.add(make_invite(org.id, manager.id, email="b@example.com", invite_code="code-b"))
    await db_session.flush()
    await db_session.refresh(org, ["invites"])

    assert len(org.invites) == 2
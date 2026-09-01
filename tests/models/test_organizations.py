"""
Organization and OrganizationInvite model tests.

REVISED for the branch/role redesign:
  - User.branch_id is NOT NULL now. make_member() takes a required
    branch_id parameter — every call site below passes owner.branch_id
    (the org's Headquarters branch, auto-created by make_org_with_owner)
    since these tests aren't about branch assignment specifically.
  - Department.branch_id is NOT NULL now. make_invite_deps() takes a
    required branch_id and creates the Department under it.
  - OrganizationInvite.branch_id is NOT NULL now (was optional before
    this redesign — see OrganizationInvite model). make_invite() takes
    a required branch_id parameter. test_invite_optional_fields_nullable
    no longer asserts invite.branch_id is None (that's now impossible)
    — it asserts the opposite: branch_id is always set, matching what
    was passed in.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone

from app.models import Organization, OrganizationInvite, Role, User
from app.models.department import Department
from tests.conftest import (
    get_or_create_system_role,
    make_org_with_owner,
    make_user,
    make_user_with_role,
)


# ── Factories ──────────────────────────────────────────────────────────────────

async def make_member(
    db_session: AsyncSession, organization_id: str, branch_id: str, **overrides
) -> User:
    """A non-owner member of an existing org — analogous to a user who
    joined via accepted invite. organization_id must already exist.
    branch_id is now REQUIRED (User.branch_id is NOT NULL) — pass the
    org's home branch (e.g. owner.branch_id) unless the test is
    specifically about branch assignment.

    role_name (if not overridden) defaults to "member" and is
    resolved to a real system Role's id via make_user_with_role()."""
    overrides.setdefault("email", "member@example.com")
    overrides.setdefault("role_name", "member")
    user = await make_user_with_role(
        db_session, organization_id=organization_id, branch_id=branch_id, **overrides
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def make_invite_deps(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
) -> tuple[Department, Role]:
    """
    Create the minimum Department and Role rows required by
    OrganizationInvite's NOT NULL FKs (invited_department_id,
    invited_role_id). branch_id is now REQUIRED — Department.branch_id
    is NOT NULL, so the department must be created under a real branch.

    Returns (department, role) so callers can pass their IDs into
    make_invite(). Role is resolved via get_or_create_system_role() so
    it is safe to call multiple times within the same test session
    without hitting a unique violation.
    """
    department = Department(
        name="General",
        organization_id=organization_id,
        branch_id=branch_id,
    )
    db_session.add(department)
    await db_session.flush()

    role = await get_or_create_system_role(db_session, "member")

    return department, role


def make_invite(
    organization_id: str,
    invited_by_id: str,
    invited_department_id: str,
    invited_role_id: str,
    branch_id: str,
    email: str = "guest@example.com",
    **overrides,
) -> OrganizationInvite:
    """
    Construct an OrganizationInvite with all NOT NULL fields supplied.

    invited_department_id, invited_role_id, AND branch_id are all
    required now (branch_id was optional before this redesign — see
    OrganizationInvite model docstring). Get department/role from
    make_invite_deps() before calling this factory, and pass the same
    branch_id used to create that department.
    """
    defaults = dict(
        organization_id=organization_id,
        branch_id=branch_id,
        invited_email=email,
        invite_code="some-secure-token-123",
        status="pending",
        invited_by=invited_by_id,
        invited_department_id=invited_department_id,
        invited_role_id=invited_role_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    return OrganizationInvite(**{**defaults, **overrides})


# ── Organization model ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_persists(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session, with_trial=False)

    result = await db_session.execute(select(Organization).where(Organization.id == org.id))
    fetched = result.scalar_one()
    assert fetched.business_name == "Acme Corp"
    assert fetched.slug == "acme-corp"
    assert fetched.owner_id == user.id


@pytest.mark.asyncio
async def test_organization_id_is_uuid_format(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)

    parts = org.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_organization_timestamps_auto_set(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    assert org.created_at is not None
    assert org.updated_at is not None


@pytest.mark.asyncio
async def test_organization_slug_unique(db_session: AsyncSession):
    """Two orgs cannot share the same slug."""
    await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(slug="same-slug"),
        user_kwargs=dict(email="owner1@example.com"),
    )

    org2 = Organization(business_name="Other Corp", slug="same-slug", owner_id=None)
    db_session.add(org2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_optional_fields_nullable(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    assert org.business_website is None
    assert org.employee_count is None
    assert org.organization_type is None
    assert org.work_model is None


@pytest.mark.asyncio
async def test_organization_full_fields(db_session: AsyncSession):
    org, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(
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
    org, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(slug=f"org-{org_type}", organization_type=org_type),
        user_kwargs=dict(email=f"{org_type}@example.com"),
    )
    assert org.organization_type == org_type


@pytest.mark.asyncio
@pytest.mark.parametrize("work_model", ["remote", "hybrid", "onsite"])
async def test_organization_accepts_any_work_model(db_session: AsyncSession, work_model: str):
    org, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(slug=f"org-{work_model}", work_model=work_model),
        user_kwargs=dict(email=f"{work_model}@example.com"),
    )
    assert org.work_model == work_model


@pytest.mark.asyncio
async def test_organization_owner_fk_must_exist(db_session: AsyncSession):
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
        "contract forbids it. To make this test pass, add a DEFERRABLE INITIALLY "
        "DEFERRED NOT NULL constraint on owner_id (PostgreSQL only) and remove xfail."
    ),
    strict=True,
)
async def test_committed_org_owner_id_cannot_be_null(db_session: AsyncSession):
    org = Organization(
        business_name="Ownerless Corp",
        slug="ownerless-corp",
        owner_id=None,
    )
    db_session.add(org)
    await db_session.flush()
    await db_session.commit()

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
    it mirrors) must leave the database in a fully linked state,
    including the owner having a real home branch (HQ)."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    assert org.owner_id == owner.id
    assert owner.organization_id == org.id
    assert owner.branch_id is not None  # HQ branch, auto-created

    await db_session.refresh(org, ["owner"])
    assert org.owner is not None
    assert org.owner.id == owner.id
    assert org.owner.organization_id == org.id


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "SQLite does not reliably fire ondelete=CASCADE on deferred/use_alter FKs "
        "(owner_id uses use_alter=True for circular FK resolution). This cascade chain "
        "DOES fire correctly in PostgreSQL (production). Documents intended behavior."
    ),
    strict=True,
)
async def test_deleting_owner_directly_cascades_entire_org(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    member = await make_member(db_session, org.id, owner.branch_id, email="bystander@example.com")

    org_id = org.id
    owner_id = owner.id
    member_id = member.id

    await db_session.delete(owner)
    await db_session.flush()
    db_session.expire_all()

    org_result = await db_session.execute(
        select(Organization).where(Organization.id == org_id)
    )
    assert org_result.scalar_one_or_none() is None

    member_result = await db_session.execute(select(User).where(User.id == member_id))
    assert member_result.scalar_one_or_none() is None

    owner_result = await db_session.execute(select(User).where(User.id == owner_id))
    assert owner_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_organization_id_required(db_session: AsyncSession):
    """organization_id is NOT NULL — a User cannot be created without a resolved organization."""
    role = await get_or_create_system_role(db_session, "member")
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
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    owner_id = owner.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id == owner_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_organization_deletion_cascades_to_all_members(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    member1 = await make_member(db_session, org.id, owner.branch_id, email="member1@example.com")
    member2 = await make_member(db_session, org.id, owner.branch_id, email="member2@example.com")

    member_ids = [owner.id, member1.id, member2.id]

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id.in_(member_ids)))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_non_owner_member_does_not_delete_org(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    member = await make_member(db_session, org.id, owner.branch_id, email="leaving@example.com")

    await db_session.delete(member)
    await db_session.flush()

    org_result = await db_session.execute(select(Organization).where(Organization.id == org.id))
    assert org_result.scalar_one_or_none() is not None

    owner_result = await db_session.execute(select(User).where(User.id == owner.id))
    assert owner_result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_one_user_can_own_multiple_orgs(db_session: AsyncSession):
    org1, owner = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(slug="org-one", business_name="Org One"),
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
        with_trial=False, org_kwargs=dict(slug="org-active-default"),
        user_kwargs=dict(email="active_default@example.com"),
    )
    assert org.is_active is True
    assert org.deactivated_at is None


@pytest.mark.asyncio
async def test_organization_can_be_deactivated(db_session: AsyncSession):
    org, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(slug="org-deactivate"),
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
    org, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(slug="org-reactivate"),
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
    """Deactivating an org is reversible — its related rows survive untouched."""
    org, manager = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(slug="org-deactivate-children"),
        user_kwargs=dict(email="deactivate_children@example.com"),
    )
    member = await make_member(db_session, org.id, manager.branch_id, email="stays@example.com")

    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)
    invite = make_invite(
        org.id, manager.id,
        invited_department_id=dept.id,
        invited_role_id=role.id,
        branch_id=manager.branch_id,
        email="stillhere@example.com",
    )
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
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    await db_session.refresh(org, ["owner"])

    assert org.owner is not None
    assert org.owner.id == owner.id
    assert org.owner.email == "owner@example.com"


@pytest.mark.asyncio
async def test_user_owned_organizations_relationship(db_session: AsyncSession):
    org1, owner = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(slug="org-a", business_name="Org A"),
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
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    member1 = await make_member(db_session, org.id, owner.branch_id, email="m1@example.com")
    member2 = await make_member(db_session, org.id, owner.branch_id, email="m2@example.com")

    await db_session.refresh(org, ["members"])

    member_ids = {m.id for m in org.members}
    assert member_ids == {owner.id, member1.id, member2.id}


# ── OrganizationInvite model ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_invite_persists(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        org.id, manager.id,
        invited_department_id=dept.id,
        invited_role_id=role.id,
        branch_id=manager.branch_id,
        email="newhire@example.com",
    )
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
    assert fetched.invited_department_id == dept.id
    assert fetched.invited_role_id == role.id
    assert fetched.branch_id == manager.branch_id


@pytest.mark.asyncio
async def test_invite_id_is_uuid_format(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        org.id, manager.id, invited_department_id=dept.id, invited_role_id=role.id,
        branch_id=manager.branch_id,
    )
    db_session.add(invite)
    await db_session.flush()

    parts = invite.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_invite_timestamps_auto_set(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        org.id, manager.id, invited_department_id=dept.id, invited_role_id=role.id,
        branch_id=manager.branch_id,
    )
    db_session.add(invite)
    await db_session.flush()

    assert invite.created_at is not None
    assert invite.updated_at is not None
    assert invite.expires_at is not None


@pytest.mark.asyncio
async def test_invite_default_status_is_pending(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        org.id, manager.id, invited_department_id=dept.id, invited_role_id=role.id,
        branch_id=manager.branch_id,
    )
    db_session.add(invite)
    await db_session.flush()

    assert invite.status == "pending"


@pytest.mark.asyncio
async def test_invite_optional_fields_nullable(db_session: AsyncSession):
    """
    REVISED: branch_id used to be optional on OrganizationInvite and
    this test asserted invite.branch_id is None. branch_id is NOT NULL
    now (every invite must target a branch) — this test now asserts
    the opposite: branch_id is always set to whatever was passed in.
    accepted_at/accepted_by_id remain genuinely optional.
    """
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        org.id, manager.id, invited_department_id=dept.id, invited_role_id=role.id,
        branch_id=manager.branch_id,
    )
    db_session.add(invite)
    await db_session.flush()

    assert invite.branch_id == manager.branch_id
    assert invite.accepted_at is None
    assert invite.accepted_by_id is None


@pytest.mark.asyncio
async def test_invite_code_unique(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    db_session.add(make_invite(
        org.id, manager.id,
        invited_department_id=dept.id,
        invited_role_id=role.id,
        branch_id=manager.branch_id,
        invite_code="same-code",
    ))
    await db_session.flush()

    db_session.add(make_invite(
        org.id, manager.id,
        invited_department_id=dept.id,
        invited_role_id=role.id,
        branch_id=manager.branch_id,
        email="other@example.com",
        invite_code="same-code",
    ))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_invite_org_fk_must_exist(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        organization_id="00000000-0000-0000-0000-000000000000",
        invited_by_id=manager.id,
        invited_department_id=dept.id,
        invited_role_id=role.id,
        branch_id=manager.branch_id,
    )
    db_session.add(invite)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_cascades_to_invites(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="boss@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        org.id, manager.id, invited_department_id=dept.id, invited_role_id=role.id,
        branch_id=manager.branch_id,
    )
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
    """ondelete=SET NULL on accepted_by_id — invite row survives a non-owner member's deletion."""
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)
    acceptor = await make_member(db_session, org.id, manager.branch_id, email="acceptor@example.com")

    invite = make_invite(
        org.id, manager.id,
        invited_department_id=dept.id,
        invited_role_id=role.id,
        branch_id=manager.branch_id,
        accepted_by_id=acceptor.id,
    )
    db_session.add(invite)
    await db_session.flush()

    await db_session.delete(acceptor)
    await db_session.flush()
    await db_session.refresh(invite)

    assert invite.accepted_by_id is None
    assert invite.id is not None


# ── OrganizationInvite Relationships ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_invite_dual_user_relationships(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="sender@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        org.id, manager.id,
        invited_department_id=dept.id,
        invited_role_id=role.id,
        branch_id=manager.branch_id,
        email="receiver@example.com",
    )
    db_session.add(invite)
    await db_session.flush()

    new_user = await make_member(db_session, org.id, manager.branch_id, email="receiver@example.com")

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
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        org.id, manager.id, invited_department_id=dept.id, invited_role_id=role.id,
        branch_id=manager.branch_id,
    )
    db_session.add(invite)
    await db_session.flush()
    await db_session.refresh(invite, ["organization"])

    assert invite.organization is not None
    assert invite.organization.id == org.id


@pytest.mark.asyncio
async def test_invite_inviter_relationship(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    invite = make_invite(
        org.id, manager.id, invited_department_id=dept.id, invited_role_id=role.id,
        branch_id=manager.branch_id,
    )
    db_session.add(invite)
    await db_session.flush()
    await db_session.refresh(invite, ["inviter"])

    assert invite.inviter is not None
    assert invite.inviter.id == manager.id


@pytest.mark.asyncio
async def test_organization_invites_relationship(db_session: AsyncSession):
    org, manager = await make_org_with_owner(
        db_session, with_trial=False, user_kwargs=dict(email="manager@example.com")
    )
    dept, role = await make_invite_deps(db_session, org.id, manager.branch_id)

    db_session.add(make_invite(
        org.id, manager.id,
        invited_department_id=dept.id,
        invited_role_id=role.id,
        branch_id=manager.branch_id,
        email="a@example.com",
        invite_code="code-a",
    ))
    db_session.add(make_invite(
        org.id, manager.id,
        invited_department_id=dept.id,
        invited_role_id=role.id,
        branch_id=manager.branch_id,
        email="b@example.com",
        invite_code="code-b",
    ))
    await db_session.flush()
    await db_session.refresh(org, ["invites"])

    assert len(org.invites) == 2
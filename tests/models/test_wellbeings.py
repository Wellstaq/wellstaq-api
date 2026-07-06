"""
WellbeingDimension, WellbeingChallenge, WellbeingScore,
OrganizationWellbeingChallenge, UserWellbeingBaseline, and
UserHealthAssessment model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

organization_id is present on WellbeingScore, UserWellbeingBaseline, and
UserHealthAssessment for tenant isolation. Tests cover org scoping,
cross-tenant isolation, and cascade deletes through organization_id on
all three tables.

Factory note:
setup_org() here is a thin wrapper around the shared
tests.conftest.make_org_with_owner(), which resolves the circular FK
between Organization.owner_id and User.organization_id the same way
the real signup transaction does (see conftest.py docstring). This
file's setup_org() previously built User and Organization directly and
broke once User.organization_id became NOT NULL — exactly the failure
mode conftest.make_org_with_owner() exists to prevent. The wrapper
keeps this file's existing (user, org) return order and
(email, slug) call signature so none of the ~25 call sites below
needed to change, while delegating the actual creation logic to the
single shared implementation.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Organization,
    OrganizationWellbeingChallenge,
    User,
    WellbeingChallenge,
    UserWellbeingBaseline,
    UserHealthAssessment,
    HealthCategory,
    WellbeingDimension,
    WellbeingScore,
)
from tests.conftest import make_user, make_org_with_owner, make_user_with_role


# ── Factories ──────────────────────────────────────────────────────────────────

def make_dimension(name: str = "Physical", **overrides) -> WellbeingDimension:
    return WellbeingDimension(name=name, **overrides)


def make_challenge(name: str = "30-Day Step Challenge", **overrides) -> WellbeingChallenge:
    return WellbeingChallenge(name=name, **overrides)


def make_score(
    organization_id: str,
    user_id: str,
    dimension_id: str,
    **overrides,
) -> WellbeingScore:
    defaults = dict(
        organization_id=organization_id,
        user_id=user_id,
        dimension_id=dimension_id,
        score=7.5,
        period="2026-06",
    )
    return WellbeingScore(**{**defaults, **overrides})


def make_org_wellbeing_challenge(
    organization_id: str,
    wellbeing_challenge_id: str,
    **overrides,
) -> OrganizationWellbeingChallenge:
    defaults = dict(
        organization_id=organization_id,
        wellbeing_challenge_id=wellbeing_challenge_id,
    )
    return OrganizationWellbeingChallenge(**{**defaults, **overrides})


def make_baseline(
    organization_id: str,
    user_id: str,
    **overrides,
) -> UserWellbeingBaseline:
    defaults = dict(
        organization_id=organization_id,
        user_id=user_id,
        dimension="mood",
        level="good",
        reason="work",
    )
    return UserWellbeingBaseline(**{**defaults, **overrides})


def make_health_category(name: str = "Cardiovascular", **overrides) -> HealthCategory:
    return HealthCategory(name=name, **overrides)


def make_health_assessment(
    organization_id: str,
    user_id: str,
    **overrides,
) -> UserHealthAssessment:
    defaults = dict(
        organization_id=organization_id,
        user_id=user_id,
        status="pending",
    )
    return UserHealthAssessment(**{**defaults, **overrides})


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(
    db_session: AsyncSession,
    email: str = "owner@example.com",
    slug: str = "acme-corp",
) -> tuple[User, Organization]:
    """Thin wrapper around the shared make_org_with_owner() factory —
    see module docstring. Preserves this file's original (user, org)
    return order and (email, slug) signature so existing call sites
    below didn't need to change."""
    org, user = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=slug),
        user_kwargs=dict(email=email, role_name="overall_manager"),
    )
    return user, org

async def make_member(
    db_session: AsyncSession, organization_id: str, branch_id: str, **overrides
) -> User:
    """A non-owner member of an existing org. organization_id must
    already exist (i.e. come from a setup_org() call). branch_id is
    now REQUIRED (User.branch_id is NOT NULL) — pass the org's home
    branch (e.g. owner.branch_id from setup_org())."""
    overrides.setdefault("email", "member@example.com")
    role_name = overrides.pop("role_name", "employee")
    user = await make_user_with_role(
        db_session,
        organization_id=organization_id,
        branch_id=branch_id,
        role_name=role_name,
        **overrides,
    )
    db_session.add(user)
    await db_session.flush()
    return user

# ── WellbeingDimension ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dimension_persists(db_session: AsyncSession):
    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingDimension).where(WellbeingDimension.id == dimension.id)
    )
    fetched = result.scalar_one()
    assert fetched.name == "Physical"


@pytest.mark.asyncio
async def test_dimension_id_is_uuid_format(db_session: AsyncSession):
    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    parts = dimension.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_multiple_dimensions_can_exist(db_session: AsyncSession):
    db_session.add(make_dimension(name="Physical"))
    db_session.add(make_dimension(name="Mental"))
    db_session.add(make_dimension(name="Financial"))
    await db_session.flush()

    result = await db_session.execute(select(WellbeingDimension))
    names = {d.name for d in result.scalars().all()}
    assert names == {"Physical", "Mental", "Financial"}


# ── WellbeingChallenge ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_challenge_persists(db_session: AsyncSession):
    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingChallenge).where(WellbeingChallenge.id == challenge.id)
    )
    fetched = result.scalar_one()
    assert fetched.name == "30-Day Step Challenge"


@pytest.mark.asyncio
async def test_challenge_id_is_uuid_format(db_session: AsyncSession):
    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    parts = challenge.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_multiple_challenges_can_exist(db_session: AsyncSession):
    db_session.add(make_challenge(name="30-Day Step Challenge"))
    db_session.add(make_challenge(name="Hydration Week"))
    await db_session.flush()

    result = await db_session.execute(select(WellbeingChallenge))
    names = {c.name for c in result.scalars().all()}
    assert names == {"30-Day Step Challenge", "Hydration Week"}


# ── WellbeingScore ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_persists(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(org.id, user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(WellbeingScore.id == score.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.user_id == user.id
    assert fetched.dimension_id == dimension.id
    assert float(fetched.score) == 7.5
    assert fetched.period == "2026-06"


@pytest.mark.asyncio
async def test_score_id_is_uuid_format(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(org.id, user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()

    parts = score.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_score_supports_decimal_precision(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(org.id, user.id, dimension.id, score=8.75)
    db_session.add(score)
    await db_session.flush()
    await db_session.refresh(score)

    assert float(score.score) == 8.75


@pytest.mark.asyncio
async def test_score_org_fk_must_exist(db_session: AsyncSession):
    owner, org = await setup_org(db_session, email="placeholder@example.com")

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    member = await make_member(db_session, org.id, owner.branch_id, email="scope-test@example.com")

    score = make_score(
        organization_id="00000000-0000-0000-0000-000000000000",
        user_id=member.id,
        dimension_id=dimension.id,
    )
    db_session.add(score)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_score_user_fk_must_exist(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(
        organization_id=org.id,
        user_id="00000000-0000-0000-0000-000000000000",
        dimension_id=dimension.id,
    )
    db_session.add(score)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_score_dimension_fk_must_exist(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    score = make_score(
        organization_id=org.id,
        user_id=user.id,
        dimension_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(score)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_score_filterable_by_org(db_session: AsyncSession):
    """Scores from org A are directly queryable by organization_id."""
    user_a, org_a = await setup_org(db_session, email="owner_a@example.com", slug="org-a")
    user_b, org_b = await setup_org(db_session, email="owner_b@example.com", slug="org-b")

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    db_session.add(make_score(org_a.id, user_a.id, dimension.id, score=7.0))
    db_session.add(make_score(org_b.id, user_b.id, dimension.id, score=9.0))
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(WellbeingScore.organization_id == org_a.id)
    )
    scores = result.scalars().all()
    assert len(scores) == 1
    assert float(scores[0].score) == 7.0


@pytest.mark.asyncio
async def test_score_cross_tenant_isolation(db_session: AsyncSession):
    """Org B cannot see org A's scores when filtering by org_id."""
    user_a, org_a = await setup_org(db_session, email="owner_a@example.com", slug="org-a")
    user_b, org_b = await setup_org(db_session, email="owner_b@example.com", slug="org-b")

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    db_session.add(make_score(org_a.id, user_a.id, dimension.id))
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(WellbeingScore.organization_id == org_b.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_user_can_have_multiple_dimension_scores_same_period(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    physical = make_dimension(name="Physical")
    mental = make_dimension(name="Mental")
    db_session.add_all([physical, mental])
    await db_session.flush()

    db_session.add(make_score(org.id, user.id, physical.id, period="2026-06", score=7.0))
    db_session.add(make_score(org.id, user.id, mental.id, period="2026-06", score=8.0))
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(
            WellbeingScore.organization_id == org.id,
            WellbeingScore.user_id == user.id,
            WellbeingScore.period == "2026-06",
        )
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_user_can_have_same_dimension_across_periods(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    db_session.add(make_score(org.id, user.id, dimension.id, period="2026-05", score=6.0))
    db_session.add(make_score(org.id, user.id, dimension.id, period="2026-06", score=7.5))
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(
            WellbeingScore.organization_id == org.id,
            WellbeingScore.user_id == user.id,
            WellbeingScore.dimension_id == dimension.id,
        )
    )
    periods = {s.period for s in result.scalars().all()}
    assert periods == {"2026-05", "2026-06"}


@pytest.mark.asyncio
async def test_org_delete_cascades_to_scores(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(org.id, user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()
    score_id = score.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(WellbeingScore.id == score_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_delete_cascades_to_scores(db_session: AsyncSession):
    # Setup organization (and its protected owner user)
    owner, org = await setup_org(db_session)

    # Create a separate member user who is NOT the organization owner
    member = await make_member(db_session, org.id, owner.branch_id, email="member@example.com")

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(org.id, member.id, dimension.id)
    db_session.add(score)
    await db_session.flush()
    score_id = score.id

    # Delete the member, leaving the owner completely intact
    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(WellbeingScore.id == score_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_dimension_delete_cascades_to_scores(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(org.id, user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()

    await db_session.delete(dimension)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(WellbeingScore.id == score.id)
    )
    assert result.scalar_one_or_none() is None


# ── WellbeingScore relationships ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_organization_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(org.id, user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()
    await db_session.refresh(score, ["organization"])

    assert score.organization is not None
    assert score.organization.id == org.id


@pytest.mark.asyncio
async def test_score_user_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(org.id, user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()
    await db_session.refresh(score, ["user"])

    assert score.user is not None
    assert score.user.id == user.id


@pytest.mark.asyncio
async def test_score_dimension_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(org.id, user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()
    await db_session.refresh(score, ["dimension"])

    assert score.dimension is not None
    assert score.dimension.id == dimension.id


@pytest.mark.asyncio
async def test_organization_wellbeing_scores_relationship(db_session: AsyncSession):
    """org.wellbeing_scores loads all WellbeingScore rows for that org."""
    user, org = await setup_org(db_session)

    physical = make_dimension(name="Physical")
    mental = make_dimension(name="Mental")
    db_session.add_all([physical, mental])
    await db_session.flush()

    db_session.add(make_score(org.id, user.id, physical.id))
    db_session.add(make_score(org.id, user.id, mental.id))
    await db_session.flush()
    await db_session.refresh(org, ["wellbeing_scores"])

    assert len(org.wellbeing_scores) == 2


@pytest.mark.asyncio
async def test_dimension_scores_relationship(db_session: AsyncSession):
    user1, org1 = await setup_org(db_session, email="o1@example.com", slug="org-1")
    user2, org2 = await setup_org(db_session, email="o2@example.com", slug="org-2")

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    db_session.add(make_score(org1.id, user1.id, dimension.id))
    db_session.add(make_score(org2.id, user2.id, dimension.id))
    await db_session.flush()
    await db_session.refresh(dimension, ["scores"])

    assert len(dimension.scores) == 2


@pytest.mark.asyncio
async def test_user_wellbeing_scores_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    physical = make_dimension(name="Physical")
    mental = make_dimension(name="Mental")
    db_session.add_all([physical, mental])
    await db_session.flush()

    db_session.add(make_score(org.id, user.id, physical.id))
    db_session.add(make_score(org.id, user.id, mental.id))
    await db_session.flush()
    await db_session.refresh(user, ["wellbeing_scores"])

    assert len(user.wellbeing_scores) == 2


# ── OrganizationWellbeingChallenge ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_wellbeing_challenge_persists(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    link = make_org_wellbeing_challenge(org.id, challenge.id)
    db_session.add(link)
    await db_session.flush()

    result = await db_session.execute(
        select(OrganizationWellbeingChallenge).where(OrganizationWellbeingChallenge.id == link.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.wellbeing_challenge_id == challenge.id


@pytest.mark.asyncio
async def test_org_wellbeing_challenge_id_is_uuid_format(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    link = make_org_wellbeing_challenge(org.id, challenge.id)
    db_session.add(link)
    await db_session.flush()

    parts = link.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_organization_cannot_opt_into_same_challenge_twice(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    db_session.add(make_org_wellbeing_challenge(org.id, challenge.id))
    await db_session.flush()

    db_session.add(make_org_wellbeing_challenge(org.id, challenge.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_wellbeing_challenge_org_fk_must_exist(db_session: AsyncSession):
    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    link = make_org_wellbeing_challenge(
        organization_id="00000000-0000-0000-0000-000000000000",
        wellbeing_challenge_id=challenge.id,
    )
    db_session.add(link)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_wellbeing_challenge_challenge_fk_must_exist(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    link = make_org_wellbeing_challenge(
        organization_id=org.id,
        wellbeing_challenge_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(link)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_delete_cascades_to_wellbeing_challenges(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    link = make_org_wellbeing_challenge(org.id, challenge.id)
    db_session.add(link)
    await db_session.flush()
    link_id = link.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(OrganizationWellbeingChallenge).where(OrganizationWellbeingChallenge.id == link_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_wellbeing_challenge_delete_cascades_to_links(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    link = make_org_wellbeing_challenge(org.id, challenge.id)
    db_session.add(link)
    await db_session.flush()
    link_id = link.id

    await db_session.delete(challenge)
    await db_session.flush()

    result = await db_session.execute(
        select(OrganizationWellbeingChallenge).where(OrganizationWellbeingChallenge.id == link_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_org_wellbeing_challenge_organization_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    link = make_org_wellbeing_challenge(org.id, challenge.id)
    db_session.add(link)
    await db_session.flush()
    await db_session.refresh(link, ["organization"])

    assert link.organization is not None
    assert link.organization.id == org.id


@pytest.mark.asyncio
async def test_org_wellbeing_challenge_challenge_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    link = make_org_wellbeing_challenge(org.id, challenge.id)
    db_session.add(link)
    await db_session.flush()
    await db_session.refresh(link, ["wellbeing_challenge"])

    assert link.wellbeing_challenge is not None
    assert link.wellbeing_challenge.id == challenge.id


@pytest.mark.asyncio
async def test_organization_wellbeing_challenges_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    fitness = make_challenge(name="Fitness")
    mental_health = make_challenge(name="Mental Health")
    db_session.add_all([fitness, mental_health])
    await db_session.flush()

    db_session.add(make_org_wellbeing_challenge(org.id, fitness.id))
    db_session.add(make_org_wellbeing_challenge(org.id, mental_health.id))
    await db_session.flush()
    await db_session.refresh(org, ["wellbeing_challenges"])

    assert len(org.wellbeing_challenges) == 2


@pytest.mark.asyncio
async def test_wellbeing_challenge_organizations_relationship(db_session: AsyncSession):
    user1, org_a = await setup_org(db_session, email="owner1@example.com", slug="org-a")
    user2, org_b = await setup_org(db_session, email="owner2@example.com", slug="org-b")

    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    db_session.add(make_org_wellbeing_challenge(org_a.id, challenge.id))
    db_session.add(make_org_wellbeing_challenge(org_b.id, challenge.id))
    await db_session.flush()
    await db_session.refresh(challenge, ["organizations"])

    assert len(challenge.organizations) == 2


# ── UserWellbeingBaseline ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_baseline_persists(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    baseline = make_baseline(org.id, user.id)
    db_session.add(baseline)
    await db_session.flush()

    result = await db_session.execute(
        select(UserWellbeingBaseline).where(UserWellbeingBaseline.id == baseline.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.dimension == "mood"
    assert fetched.level == "good"
    assert fetched.reason == "work"
    assert fetched.recorded_at is not None


@pytest.mark.asyncio
async def test_baseline_id_is_uuid_format(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    baseline = make_baseline(org.id, user.id)
    db_session.add(baseline)
    await db_session.flush()

    parts = baseline.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_baseline_filterable_by_org(db_session: AsyncSession):
    """Baselines from org A are directly queryable by organization_id."""
    user_a, org_a = await setup_org(db_session, email="owner_a@example.com", slug="org-a")
    user_b, org_b = await setup_org(db_session, email="owner_b@example.com", slug="org-b")

    db_session.add(make_baseline(org_a.id, user_a.id, level="good"))
    db_session.add(make_baseline(org_b.id, user_b.id, level="bad"))
    await db_session.flush()

    result = await db_session.execute(
        select(UserWellbeingBaseline).where(
            UserWellbeingBaseline.organization_id == org_a.id
        )
    )
    baselines = result.scalars().all()
    assert len(baselines) == 1
    assert baselines[0].level == "good"


@pytest.mark.asyncio
async def test_baseline_cross_tenant_isolation(db_session: AsyncSession):
    user_a, org_a = await setup_org(db_session, email="owner_a@example.com", slug="org-a")
    user_b, org_b = await setup_org(db_session, email="owner_b@example.com", slug="org-b")

    db_session.add(make_baseline(org_a.id, user_a.id))
    await db_session.flush()

    result = await db_session.execute(
        select(UserWellbeingBaseline).where(
            UserWellbeingBaseline.organization_id == org_b.id
        )
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_user_can_have_baselines_for_multiple_dimensions(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    db_session.add(make_baseline(org.id, user.id, dimension="mood", level="good"))
    db_session.add(make_baseline(org.id, user.id, dimension="stress", level="stressed"))
    db_session.add(make_baseline(org.id, user.id, dimension="energy", level="tired"))
    db_session.add(make_baseline(org.id, user.id, dimension="work_life_balance", level="bad"))
    await db_session.flush()

    result = await db_session.execute(
        select(UserWellbeingBaseline).where(
            UserWellbeingBaseline.organization_id == org.id,
            UserWellbeingBaseline.user_id == user.id,
        )
    )
    dimensions = {b.dimension for b in result.scalars().all()}
    assert dimensions == {"mood", "stress", "energy", "work_life_balance"}


@pytest.mark.asyncio
async def test_baseline_reason_is_optional(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    baseline = make_baseline(org.id, user.id, reason=None)
    db_session.add(baseline)
    await db_session.flush()

    assert baseline.reason is None


@pytest.mark.asyncio
async def test_baseline_org_fk_must_exist(db_session: AsyncSession):
    owner, org = await setup_org(db_session, email="placeholder2@example.com")
    member = await make_member(db_session, org.id, owner.branch_id, email="baseline-fk-test@example.com")

    baseline = make_baseline(
        organization_id="00000000-0000-0000-0000-000000000000",
        user_id=member.id,
    )
    db_session.add(baseline)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_baseline_user_fk_must_exist(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    baseline = make_baseline(
        organization_id=org.id,
        user_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(baseline)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_delete_cascades_to_baselines(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    baseline = make_baseline(org.id, user.id)
    db_session.add(baseline)
    await db_session.flush()
    baseline_id = baseline.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(UserWellbeingBaseline).where(UserWellbeingBaseline.id == baseline_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_delete_cascades_to_baselines(db_session: AsyncSession):
    # Setup organization (and its protected owner user)
    owner, org = await setup_org(db_session)

    # Create a separate member user who is NOT the organization owner
    member = await make_member(db_session, org.id, owner.branch_id, email="member@example.com")

    baseline = make_baseline(org.id, member.id)
    db_session.add(baseline)
    await db_session.flush()
    baseline_id = baseline.id

    # Delete the member, leaving the owner completely intact
    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(
        select(UserWellbeingBaseline).where(UserWellbeingBaseline.id == baseline_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_baseline_organization_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    baseline = make_baseline(org.id, user.id)
    db_session.add(baseline)
    await db_session.flush()
    await db_session.refresh(baseline, ["organization"])

    assert baseline.organization is not None
    assert baseline.organization.id == org.id


@pytest.mark.asyncio
async def test_baseline_user_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    baseline = make_baseline(org.id, user.id)
    db_session.add(baseline)
    await db_session.flush()
    await db_session.refresh(baseline, ["user"])

    assert baseline.user is not None
    assert baseline.user.id == user.id


@pytest.mark.asyncio
async def test_organization_wellbeing_baselines_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    db_session.add(make_baseline(org.id, user.id, dimension="mood"))
    db_session.add(make_baseline(org.id, user.id, dimension="stress"))
    await db_session.flush()
    await db_session.refresh(org, ["wellbeing_baselines"])

    assert len(org.wellbeing_baselines) == 2


@pytest.mark.asyncio
async def test_user_wellbeing_baselines_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    db_session.add(make_baseline(org.id, user.id, dimension="mood"))
    db_session.add(make_baseline(org.id, user.id, dimension="stress"))
    await db_session.flush()
    await db_session.refresh(user, ["wellbeing_baselines"])

    assert len(user.wellbeing_baselines) == 2


# ── UserHealthAssessment ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_assessment_persists(db_session: AsyncSession):
    user, org = await setup_org(db_session, email="owner@example.com")

    assessment = make_health_assessment(org.id, user.id)
    db_session.add(assessment)
    await db_session.flush()

    result = await db_session.execute(
        select(UserHealthAssessment).where(UserHealthAssessment.id == assessment.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.user_id == user.id
    assert fetched.status == "pending"


@pytest.mark.asyncio
async def test_health_assessment_id_is_uuid_format(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    assessment = make_health_assessment(org.id, user.id)
    db_session.add(assessment)
    await db_session.flush()

    parts = assessment.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_health_assessment_timestamps_auto_set(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    assessment = make_health_assessment(org.id, user.id)
    db_session.add(assessment)
    await db_session.flush()

    assert assessment.created_at is not None
    assert assessment.updated_at is not None


@pytest.mark.asyncio
async def test_health_assessment_default_status_is_pending(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    assessment = make_health_assessment(org.id, user.id)
    db_session.add(assessment)
    await db_session.flush()

    assert assessment.status == "pending"


@pytest.mark.asyncio
async def test_health_assessment_optional_fields_nullable(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    assessment = make_health_assessment(org.id, user.id)
    db_session.add(assessment)
    await db_session.flush()

    assert assessment.category_id is None
    assert assessment.risk_score is None
    assert assessment.assessed_at is None


@pytest.mark.asyncio
async def test_health_assessment_full_fields(db_session: AsyncSession):
    from datetime import datetime, timezone

    user, org = await setup_org(db_session)

    category = make_health_category()
    db_session.add(category)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    assessment = make_health_assessment(
        org.id,
        user.id,
        category_id=category.id,
        risk_score=72.5,
        status="completed",
        assessed_at=now,
    )
    db_session.add(assessment)
    await db_session.flush()

    result = await db_session.execute(
        select(UserHealthAssessment).where(UserHealthAssessment.id == assessment.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.category_id == category.id
    assert float(fetched.risk_score) == 72.5
    assert fetched.status == "completed"
    assert fetched.assessed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "completed", "reviewed"])
async def test_health_assessment_accepts_any_status(db_session: AsyncSession, status: str):
    user, org = await setup_org(db_session, email=f"owner-{status}@example.com", slug=f"org-{status}")

    assessment = make_health_assessment(org.id, user.id, status=status)
    db_session.add(assessment)
    await db_session.flush()
    assert assessment.status == status


@pytest.mark.asyncio
async def test_health_assessment_filterable_by_org(db_session: AsyncSession):
    """Assessments from org A are directly queryable by organization_id."""
    user_a, org_a = await setup_org(db_session, email="owner_a@example.com", slug="org-a")
    user_b, org_b = await setup_org(db_session, email="owner_b@example.com", slug="org-b")

    db_session.add(make_health_assessment(org_a.id, user_a.id, status="completed"))
    db_session.add(make_health_assessment(org_b.id, user_b.id, status="pending"))
    await db_session.flush()

    result = await db_session.execute(
        select(UserHealthAssessment).where(UserHealthAssessment.organization_id == org_a.id)
    )
    assessments = result.scalars().all()
    assert len(assessments) == 1
    assert assessments[0].status == "completed"


@pytest.mark.asyncio
async def test_health_assessment_cross_tenant_isolation(db_session: AsyncSession):
    user_a, org_a = await setup_org(db_session, email="owner_a@example.com", slug="org-a")
    user_b, org_b = await setup_org(db_session, email="owner_b@example.com", slug="org-b")

    db_session.add(make_health_assessment(org_a.id, user_a.id))
    await db_session.flush()

    result = await db_session.execute(
        select(UserHealthAssessment).where(UserHealthAssessment.organization_id == org_b.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_health_assessment_org_fk_must_exist(db_session: AsyncSession):
    owner, org = await setup_org(db_session, email="placeholder3@example.com")
    member = await make_member(db_session, org.id, owner.branch_id, email="health-fk-test@example.com")

    assessment = make_health_assessment(
        organization_id="00000000-0000-0000-0000-000000000000",
        user_id=member.id,
    )
    db_session.add(assessment)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_health_assessment_user_fk_must_exist(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    assessment = make_health_assessment(
        organization_id=org.id,
        user_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(assessment)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_health_assessment_category_fk_must_exist(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    assessment = make_health_assessment(
        org.id,
        user.id,
        category_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(assessment)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_delete_cascades_to_health_assessments(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    assessment = make_health_assessment(org.id, user.id)
    db_session.add(assessment)
    await db_session.flush()
    assessment_id = assessment.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(UserHealthAssessment).where(UserHealthAssessment.id == assessment_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_delete_cascades_to_health_assessments(db_session: AsyncSession):
    # Setup organization (and its protected owner user)
    owner, org = await setup_org(db_session)

    # Create a separate member user who is NOT the organization owner
    member = await make_member(db_session, org.id, owner.branch_id, email="member@example.com")

    assessment = make_health_assessment(org.id, member.id)
    db_session.add(assessment)
    await db_session.flush()
    assessment_id = assessment.id

    # Delete the member, leaving the owner completely intact
    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(
        select(UserHealthAssessment).where(UserHealthAssessment.id == assessment_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_health_category_delete_nullifies_category_id(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    category = make_health_category()
    db_session.add(category)
    await db_session.flush()

    assessment = make_health_assessment(org.id, user.id, category_id=category.id)
    db_session.add(assessment)
    await db_session.flush()

    await db_session.delete(category)
    await db_session.flush()
    await db_session.refresh(assessment)

    assert assessment.category_id is None
    assert assessment.id is not None


# ── UserHealthAssessment relationships ────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_assessment_organization_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    assessment = make_health_assessment(org.id, user.id)
    db_session.add(assessment)
    await db_session.flush()
    await db_session.refresh(assessment, ["organization"])

    assert assessment.organization is not None
    assert assessment.organization.id == org.id


@pytest.mark.asyncio
async def test_health_assessment_user_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    assessment = make_health_assessment(org.id, user.id)
    db_session.add(assessment)
    await db_session.flush()
    await db_session.refresh(assessment, ["user"])

    assert assessment.user is not None
    assert assessment.user.id == user.id


@pytest.mark.asyncio
async def test_health_assessment_category_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    category = make_health_category()
    db_session.add(category)
    await db_session.flush()

    assessment = make_health_assessment(org.id, user.id, category_id=category.id)
    db_session.add(assessment)
    await db_session.flush()
    await db_session.refresh(assessment, ["category"])

    assert assessment.category is not None
    assert assessment.category.id == category.id


@pytest.mark.asyncio
async def test_organization_health_assessments_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    db_session.add(make_health_assessment(org.id, user.id, status="pending"))
    db_session.add(make_health_assessment(org.id, user.id, status="completed"))
    await db_session.flush()
    await db_session.refresh(org, ["health_assessments"])

    assert len(org.health_assessments) == 2


@pytest.mark.asyncio
async def test_user_health_assessments_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)

    db_session.add(make_health_assessment(org.id, user.id, status="pending"))
    db_session.add(make_health_assessment(org.id, user.id, status="completed"))
    await db_session.flush()
    await db_session.refresh(user, ["health_assessments"])

    assert len(user.health_assessments) == 2


@pytest.mark.asyncio
async def test_health_category_assessments_relationship(db_session: AsyncSession):
    user1, org1 = await setup_org(db_session, email="o1@example.com", slug="org-1")
    user2, org2 = await setup_org(db_session, email="o2@example.com", slug="org-2")

    category = make_health_category()
    db_session.add(category)
    await db_session.flush()

    db_session.add(make_health_assessment(org1.id, user1.id, category_id=category.id))
    db_session.add(make_health_assessment(org2.id, user2.id, category_id=category.id))
    await db_session.flush()
    await db_session.refresh(category, ["assessments"])

    assert len(category.assessments) == 2
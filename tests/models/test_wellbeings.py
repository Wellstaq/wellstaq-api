"""
WellbeingDimension, WellbeingChallenge, WellbeingScore, and
OrganizationWellbeingChallenge model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
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
    WellbeingDimension,
    WellbeingScore,
)


# ── Factories ──────────────────────────────────────────────────────────────────

def make_user(email: str = "user@example.com", **overrides) -> User:
    defaults = dict(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        role_name="employee",
        hashed_password="$2b$12$fakehash",
    )
    return User(**{**defaults, **overrides})


def make_dimension(name: str = "Physical", **overrides) -> WellbeingDimension:
    return WellbeingDimension(name=name, **overrides)


def make_challenge(name: str = "30-Day Step Challenge", **overrides) -> WellbeingChallenge:
    return WellbeingChallenge(name=name, **overrides)


def make_score(user_id: str, dimension_id: str, **overrides) -> WellbeingScore:
    defaults = dict(
        user_id=user_id,
        dimension_id=dimension_id,
        score=7.5,
        period="2026-06",
    )
    return WellbeingScore(**{**defaults, **overrides})


def make_org(owner_id: str, **overrides) -> Organization:
    defaults = dict(
        business_name="Acme Corp",
        slug="acme-corp",
        owner_id=owner_id,
    )
    return Organization(**{**defaults, **overrides})


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
def make_baseline(user_id: str, **overrides) -> UserWellbeingBaseline:
    defaults = dict(
        user_id=user_id,
        dimension="mood",
        level="good",
        reason="work",
    )
    return UserWellbeingBaseline(**{**defaults, **overrides})    


# ── WellbeingDimension model ────────────────────────────────────────────────────

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


# ── WellbeingChallenge model ────────────────────────────────────────────────────

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


# ── WellbeingScore model ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_persists(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()

    result = await db_session.execute(select(WellbeingScore).where(WellbeingScore.id == score.id))
    fetched = result.scalar_one()
    assert fetched.user_id == user.id
    assert fetched.dimension_id == dimension.id
    assert float(fetched.score) == 7.5
    assert fetched.period == "2026-06"


@pytest.mark.asyncio
async def test_score_id_is_uuid_format(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()

    parts = score.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_score_supports_decimal_precision(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(user.id, dimension.id, score=8.75)
    db_session.add(score)
    await db_session.flush()
    await db_session.refresh(score)

    assert float(score.score) == 8.75


@pytest.mark.asyncio
async def test_score_user_fk_must_exist(db_session: AsyncSession):
    """user_id must reference a real user."""
    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(
        user_id="00000000-0000-0000-0000-000000000000",
        dimension_id=dimension.id,
    )
    db_session.add(score)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_score_dimension_fk_must_exist(db_session: AsyncSession):
    """dimension_id must reference a real dimension."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    score = make_score(
        user_id=user.id,
        dimension_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(score)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_can_have_multiple_dimension_scores_same_period(db_session: AsyncSession):
    """A user can have scores for different dimensions in the same period."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    physical = make_dimension(name="Physical")
    mental = make_dimension(name="Mental")
    db_session.add_all([physical, mental])
    await db_session.flush()

    db_session.add(make_score(user.id, physical.id, period="2026-06", score=7.0))
    db_session.add(make_score(user.id, mental.id, period="2026-06", score=8.0))
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(
            WellbeingScore.user_id == user.id,
            WellbeingScore.period == "2026-06",
        )
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_user_can_have_same_dimension_across_periods(db_session: AsyncSession):
    """A user can track the same dimension across multiple periods (history)."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    db_session.add(make_score(user.id, dimension.id, period="2026-05", score=6.0))
    db_session.add(make_score(user.id, dimension.id, period="2026-06", score=7.5))
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingScore).where(
            WellbeingScore.user_id == user.id,
            WellbeingScore.dimension_id == dimension.id,
        )
    )
    periods = {s.period for s in result.scalars().all()}
    assert periods == {"2026-05", "2026-06"}


@pytest.mark.asyncio
async def test_user_delete_cascades_to_scores(db_session: AsyncSession):
    """Deleting a user removes their wellbeing scores."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()

    await db_session.delete(user)
    await db_session.flush()

    result = await db_session.execute(select(WellbeingScore).where(WellbeingScore.id == score.id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_dimension_delete_cascades_to_scores(db_session: AsyncSession):
    """Deleting a dimension removes scores tied to it."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()

    await db_session.delete(dimension)
    await db_session.flush()

    result = await db_session.execute(select(WellbeingScore).where(WellbeingScore.id == score.id))
    assert result.scalar_one_or_none() is None


# ── WellbeingScore relationships ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_user_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()
    await db_session.refresh(score, ["user"])

    assert score.user is not None
    assert score.user.id == user.id


@pytest.mark.asyncio
async def test_score_dimension_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    score = make_score(user.id, dimension.id)
    db_session.add(score)
    await db_session.flush()
    await db_session.refresh(score, ["dimension"])

    assert score.dimension is not None
    assert score.dimension.id == dimension.id


@pytest.mark.asyncio
async def test_dimension_scores_relationship(db_session: AsyncSession):
    """dimension.scores loads all WellbeingScore rows for that dimension."""
    user1 = make_user(email="u1@example.com")
    user2 = make_user(email="u2@example.com")
    db_session.add_all([user1, user2])
    await db_session.flush()

    dimension = make_dimension()
    db_session.add(dimension)
    await db_session.flush()

    db_session.add(make_score(user1.id, dimension.id))
    db_session.add(make_score(user2.id, dimension.id))
    await db_session.flush()
    await db_session.refresh(dimension, ["scores"])

    assert len(dimension.scores) == 2


@pytest.mark.asyncio
async def test_user_wellbeing_scores_relationship(db_session: AsyncSession):
    """user.wellbeing_scores loads all WellbeingScore rows for that user."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    physical = make_dimension(name="Physical")
    mental = make_dimension(name="Mental")
    db_session.add_all([physical, mental])
    await db_session.flush()

    db_session.add(make_score(user.id, physical.id))
    db_session.add(make_score(user.id, mental.id))
    await db_session.flush()
    await db_session.refresh(user, ["wellbeing_scores"])

    assert len(user.wellbeing_scores) == 2


# ── OrganizationWellbeingChallenge model ────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_wellbeing_challenge_persists(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

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
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

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
    """Unique constraint on (organization_id, wellbeing_challenge_id)."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

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
    """organization_id must reference a real organization."""
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
    """wellbeing_challenge_id must reference a real wellbeing challenge."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

    link = make_org_wellbeing_challenge(
        organization_id=org.id,
        wellbeing_challenge_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(link)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_delete_cascades_to_wellbeing_challenges(db_session: AsyncSession):
    """Deleting an organization cascades to its wellbeing challenge links."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

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
    """Deleting a wellbeing challenge cascades to its org links."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

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


# ── OrganizationWellbeingChallenge relationships ────────────────────────────────

@pytest.mark.asyncio
async def test_org_wellbeing_challenge_organization_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

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
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

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
    """org.wellbeing_challenges loads all linked WellbeingChallenge categories."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

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
    """challenge.organizations loads all orgs that opted into that category."""
    user1 = make_user(email="owner1@example.com")
    db_session.add(user1)
    await db_session.flush()

    org_a = make_org(user1.id, slug="org-a", business_name="Org A")
    db_session.add(org_a)
    await db_session.flush()

    user2 = make_user(email="owner2@example.com")
    db_session.add(user2)
    await db_session.flush()

    org_b = make_org(user2.id, slug="org-b", business_name="Org B")
    db_session.add(org_b)
    await db_session.flush()

    challenge = make_challenge()
    db_session.add(challenge)
    await db_session.flush()

    db_session.add(make_org_wellbeing_challenge(org_a.id, challenge.id))
    db_session.add(make_org_wellbeing_challenge(org_b.id, challenge.id))
    await db_session.flush()
    await db_session.refresh(challenge, ["organizations"])

    assert len(challenge.organizations) == 2

   
   
   
# UserWellbeingBaseline model 



@pytest.mark.asyncio
async def test_baseline_persists(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    baseline = make_baseline(user.id)
    db_session.add(baseline)
    await db_session.flush()

    result = await db_session.execute(
        select(UserWellbeingBaseline).where(UserWellbeingBaseline.id == baseline.id)
    )
    fetched = result.scalar_one()
    assert fetched.dimension == "mood"
    assert fetched.level == "good"
    assert fetched.reason == "work"
    assert fetched.recorded_at is not None


@pytest.mark.asyncio
async def test_baseline_id_is_uuid_format(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    baseline = make_baseline(user.id)
    db_session.add(baseline)
    await db_session.flush()

    parts = baseline.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_user_can_have_baselines_for_multiple_dimensions(db_session: AsyncSession):
    """A user gets one baseline per dimension captured at signup."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_baseline(user.id, dimension="mood", level="good"))
    db_session.add(make_baseline(user.id, dimension="stress", level="stressed"))
    db_session.add(make_baseline(user.id, dimension="energy", level="tired"))
    db_session.add(make_baseline(user.id, dimension="work_life_balance", level="bad"))
    await db_session.flush()

    result = await db_session.execute(
        select(UserWellbeingBaseline).where(UserWellbeingBaseline.user_id == user.id)
    )
    dimensions = {b.dimension for b in result.scalars().all()}
    assert dimensions == {"mood", "stress", "energy", "work_life_balance"}


@pytest.mark.asyncio
async def test_baseline_reason_is_optional(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    baseline = make_baseline(user.id, reason=None)
    db_session.add(baseline)
    await db_session.flush()

    assert baseline.reason is None


@pytest.mark.asyncio
async def test_baseline_user_fk_must_exist(db_session: AsyncSession):
    baseline = make_baseline(user_id="00000000-0000-0000-0000-000000000000")
    db_session.add(baseline)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_delete_cascades_to_baselines(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    baseline = make_baseline(user.id)
    db_session.add(baseline)
    await db_session.flush()
    baseline_id = baseline.id

    await db_session.delete(user)
    await db_session.flush()

    result = await db_session.execute(
        select(UserWellbeingBaseline).where(UserWellbeingBaseline.id == baseline_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_baseline_user_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    baseline = make_baseline(user.id)
    db_session.add(baseline)
    await db_session.flush()
    await db_session.refresh(baseline, ["user"])

    assert baseline.user is not None
    assert baseline.user.id == user.id


@pytest.mark.asyncio
async def test_user_wellbeing_baselines_relationship(db_session: AsyncSession):
    """user.wellbeing_baselines loads all baseline rows for that user."""
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_baseline(user.id, dimension="mood"))
    db_session.add(make_baseline(user.id, dimension="stress"))
    await db_session.flush()
    await db_session.refresh(user, ["wellbeing_baselines"])

    assert len(user.wellbeing_baselines) == 2
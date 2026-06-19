"""
Challenge / ChallengeParticipant model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.challenge import Challenge, ChallengeParticipant, WellbeingChallenge
from app.models.organization import Organization
from app.models.user import User


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


def make_org(owner_id: str, **overrides) -> Organization:
    defaults = dict(
        business_name="Acme Corp",
        slug="acme-corp",
        owner_id=owner_id,
    )
    return Organization(**{**defaults, **overrides})


def make_branch(organization_id: str, **overrides) -> Branch:
    defaults = dict(name="HQ", organization_id=organization_id)
    return Branch(**{**defaults, **overrides})


def make_challenge(organization_id: str, **overrides) -> Challenge:
    defaults = dict(
        name="10k Steps Challenge",
        description="Hit 10,000 steps a day",
        status="active",
        metric_type="steps",
        organization_id=organization_id,
    )
    return Challenge(**{**defaults, **overrides})


def make_participant(challenge_id: str, user_id: str, **overrides) -> ChallengeParticipant:
    return ChallengeParticipant(challenge_id=challenge_id, user_id=user_id, **overrides)

def make_wellbeing_challenge(**overrides) -> WellbeingChallenge:
    defaults = dict(name="Fitness")
    return WellbeingChallenge(**{**defaults, **overrides})


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(
    db_session: AsyncSession,
    email: str = "owner@example.com",
    slug: str = "acme-corp",
):
    user = make_user(email=email, role_name="overall_manager")
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id, slug=slug)
    db_session.add(org)
    await db_session.flush()

    return user, org


async def setup_challenge(db_session: AsyncSession, org_id: str, **overrides) -> Challenge:
    challenge = make_challenge(org_id, **overrides)
    db_session.add(challenge)
    await db_session.flush()
    return challenge
 
# ── Challenge model ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_challenge_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    challenge = await setup_challenge(db_session, org.id)

    result = await db_session.execute(
        select(Challenge).where(Challenge.id == challenge.id)
    )
    fetched = result.scalar_one()
    assert fetched.name == "10k Steps Challenge"
    assert fetched.organization_id == org.id
    assert fetched.branch_id is None


@pytest.mark.asyncio
async def test_challenge_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    challenge = await setup_challenge(db_session, org.id)

    parts = challenge.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_challenge_can_be_scoped_to_branch(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    challenge = await setup_challenge(db_session, org.id, branch_id=branch.id)

    assert challenge.branch_id == branch.id


@pytest.mark.asyncio
async def test_challenge_defaults_to_draft_status(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    challenge = Challenge(name="Mindfulness Month", organization_id=org.id)
    db_session.add(challenge)
    await db_session.flush()

    assert challenge.status == "draft"


# ── ChallengeParticipant model ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_challenge_participant_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    result = await db_session.execute(
        select(ChallengeParticipant).where(ChallengeParticipant.id == participant.id)
    )
    fetched = result.scalar_one()
    assert fetched.challenge_id == challenge.id
    assert fetched.user_id == member.id


@pytest.mark.asyncio
async def test_challenge_participant_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    parts = participant.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_challenge_participant_joined_at_auto_set(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    assert participant.joined_at is not None


@pytest.mark.asyncio
async def test_challenge_participant_duplicate_raises(db_session: AsyncSession):
    """A user can only join a given challenge once."""
    _, org = await setup_org(db_session)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_participant(challenge.id, member.id))
    await db_session.flush()

    db_session.add(make_participant(challenge.id, member.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_challenge_participant_same_user_different_challenges(db_session: AsyncSession):
    """A user can join multiple different challenges."""
    _, org = await setup_org(db_session)
    challenge_a = await setup_challenge(db_session, org.id, name="Steps Challenge")
    challenge_b = await setup_challenge(db_session, org.id, name="Hydration Challenge")

    member = make_user(email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_participant(challenge_a.id, member.id))
    db_session.add(make_participant(challenge_b.id, member.id))
    await db_session.flush()

    result = await db_session.execute(
        select(ChallengeParticipant).where(ChallengeParticipant.user_id == member.id)
    )
    assert len(result.scalars().all()) == 2


    
# ── WellbeingChallenge model ──────────────────────────────────────────────────
 
@pytest.mark.asyncio
async def test_wellbeing_challenge_persists(db_session: AsyncSession):
    theme = make_wellbeing_challenge()
    db_session.add(theme)
    await db_session.flush()
 
    result = await db_session.execute(
        select(WellbeingChallenge).where(WellbeingChallenge.id == theme.id)
    )
    fetched = result.scalar_one()
    assert fetched.name == "Fitness"
 
 
@pytest.mark.asyncio
async def test_wellbeing_challenge_id_is_uuid_format(db_session: AsyncSession):
    theme = make_wellbeing_challenge()
    db_session.add(theme)
    await db_session.flush()
 
    parts = theme.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]
 
 
@pytest.mark.asyncio
async def test_wellbeing_challenge_can_have_multiple_challenges(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    theme = make_wellbeing_challenge()
    db_session.add(theme)
    await db_session.flush()
 
    db_session.add(
        Challenge(name="10k Steps", organization_id=org.id, wellbeing_challenge_id=theme.id)
    )
    db_session.add(
        Challenge(name="Daily Walk", organization_id=org.id, wellbeing_challenge_id=theme.id)
    )
    await db_session.flush()
 
    result = await db_session.execute(
        select(Challenge).where(Challenge.wellbeing_challenge_id == theme.id)
    )
    assert len(result.scalars().all()) == 2
 
 
@pytest.mark.asyncio
async def test_challenge_can_exist_without_wellbeing_challenge(db_session: AsyncSession):
    """A challenge isn't required to belong to a theme/category."""
    _, org = await setup_org(db_session)
 
    challenge = Challenge(name="Custom Challenge", organization_id=org.id)
    db_session.add(challenge)
    await db_session.flush()
 
    assert challenge.wellbeing_challenge_id is None
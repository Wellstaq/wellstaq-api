"""
Challenge / ChallengeParticipant model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Branch,
    Challenge,
    ChallengeParticipant,
    Organization,
    User
)
from tests.conftest import make_org_with_owner, make_user


# ── Local Model Factories ──────────────────────────────────────────────────────

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


def make_participant(challenge_id: str, user_id: str, organization_id: str, **overrides) -> ChallengeParticipant:
    return ChallengeParticipant(
        challenge_id=challenge_id,
        user_id=user_id,
        organization_id=organization_id,
        **overrides,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_challenge(db_session: AsyncSession, org_id: str, **overrides) -> Challenge:
    challenge = make_challenge(org_id, **overrides)
    db_session.add(challenge)
    await db_session.flush()
    return challenge


# ── Challenge model ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_challenge_persists(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
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
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    parts = challenge.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_challenge_can_be_scoped_to_branch(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    challenge = await setup_challenge(db_session, org.id, branch_id=branch.id)

    assert challenge.branch_id == branch.id


@pytest.mark.asyncio
async def test_challenge_defaults_to_upcoming_status(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    challenge = Challenge(name="Mindfulness Month", organization_id=org.id)
    db_session.add(challenge)
    await db_session.flush()

    assert challenge.status == "upcoming"


# ── ChallengeParticipant model ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_challenge_participant_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id, challenge.organization_id)
    db_session.add(participant)
    await db_session.flush()

    result = await db_session.execute(
        select(ChallengeParticipant).where(ChallengeParticipant.id == participant.id)
    )
    fetched = result.scalar_one()
    assert fetched.challenge_id == challenge.id
    assert fetched.user_id == member.id
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_challenge_participant_id_is_uuid_format(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id, challenge.organization_id)
    db_session.add(participant)
    await db_session.flush()

    parts = participant.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_challenge_participant_joined_at_auto_set(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id, challenge.organization_id)
    db_session.add(participant)
    await db_session.flush()

    assert participant.joined_at is not None


@pytest.mark.asyncio
async def test_challenge_participant_duplicate_raises(db_session: AsyncSession):
    """A user can only join a given challenge once."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_participant(challenge.id, member.id, challenge.organization_id))
    await db_session.flush()

    db_session.add(make_participant(challenge.id, member.id, challenge.organization_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_challenge_participant_same_user_different_challenges(db_session: AsyncSession):
    """A user can join multiple different challenges."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge_a = await setup_challenge(db_session, org.id, name="Steps Challenge")
    challenge_b = await setup_challenge(db_session, org.id, name="Hydration Challenge")

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_participant(challenge_a.id, member.id, org.id))
    db_session.add(make_participant(challenge_b.id, member.id, org.id))
    await db_session.flush()

    result = await db_session.execute(
        select(ChallengeParticipant).where(ChallengeParticipant.user_id == member.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_challenge_participant_rank_validation(db_session: AsyncSession):
    """Verify that the rank field resolves correctly on standard structures."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id, org.id, rank=1)
    db_session.add(participant)
    await db_session.flush()

    assert participant.rank == 1


# ── ChallengeParticipant organization_id (denormalized tenant scoping) ─────────

@pytest.mark.asyncio
async def test_challenge_participant_organization_id_required(db_session: AsyncSession):
    """organization_id is NOT NULL — denormalized from the parent Challenge
    at insert time. See challenge.py module docstring."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    participant = ChallengeParticipant(
        challenge_id=challenge.id,
        user_id=member.id,
        organization_id=None,
    )
    db_session.add(participant)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_challenge_participant_org_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(
        challenge.id, member.id,
        organization_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(participant)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_organization_delete_cascades(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id, org.id)
    db_session.add(participant)
    await db_session.flush()
    participant_id = participant.id
    challenge_id = challenge.id

    await db_session.delete(org)
    await db_session.flush()

    result_p = await db_session.execute(
        select(ChallengeParticipant).where(ChallengeParticipant.id == participant_id)
    )
    assert result_p.scalar_one_or_none() is None

    result_c = await db_session.execute(
        select(Challenge).where(Challenge.id == challenge_id)
    )
    assert result_c.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_challenge_participant_organization_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id, org.id)
    db_session.add(participant)
    await db_session.flush()
    await db_session.refresh(participant, ["organization"])

    assert participant.organization is not None
    assert participant.organization.id == org.id


# ── Composite FK enforcement test ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_composite_fk_blocks_organization_id_mismatch(db_session: AsyncSession):
    """The composite FK constraint on ChallengeParticipant enforces that
    (challenge_id, organization_id) must match an existing row in the
    challenges table. This means a participant row whose organization_id
    does not match its challenge's organization_id is rejected at the DB
    level — multi-tenant cross-contamination is impossible even without
    service-layer validation."""
    # Create two separate organizations with unique slugs
    org_a, owner_a = await make_org_with_owner(db_session, with_trial=False)

    # Create org_b with a unique slug to avoid UNIQUE constraint violation
    org_b, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"business_name": "Beta Corp", "slug": "beta-corp"},
        user_kwargs={"email": "owner-b@example.com"}
    )

    challenge = await setup_challenge(db_session, org_a.id)

    member = make_user(organization_id=org_a.id, email="member@example.com", branch_id=owner_a.branch_id)
    db_session.add(member)
    await db_session.flush()

    # challenge belongs to org_a — pairing it with org_b violates the composite FK
    mismatched = make_participant(challenge.id, member.id, organization_id=org_b.id)
    db_session.add(mismatched)

    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Cascade deletion tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_challenge_deletion_cascades_to_participants(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = await setup_challenge(db_session, org.id)

    member = make_user(organization_id=org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(challenge.id, member.id, org.id)
    db_session.add(participant)
    await db_session.flush()
    participant_id = participant.id

    await db_session.delete(challenge)
    await db_session.flush()

    result = await db_session.execute(
        select(ChallengeParticipant).where(ChallengeParticipant.id == participant_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_branch_deletion_cascades_to_challenges(db_session: AsyncSession):
    """Exercise the database cascade without closing/rolling back the fixture."""
    from sqlalchemy import delete
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()
    challenge = await setup_challenge(db_session, org.id, branch_id=branch.id)
    challenge_id = challenge.id
    await db_session.execute(delete(Branch).where(Branch.id == branch.id))
    assert (await db_session.execute(select(Challenge).where(Challenge.id == challenge_id))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_creator_deletion_is_restricted(db_session: AsyncSession):
    from sqlalchemy import delete
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    creator = make_user(organization_id=org.id, email="creator@example.com", branch_id=owner.branch_id)
    db_session.add(creator)
    await db_session.flush()
    await setup_challenge(db_session, org.id, created_by=creator.id)
    with pytest.raises(IntegrityError):
        await db_session.execute(delete(User).where(User.id == creator.id))

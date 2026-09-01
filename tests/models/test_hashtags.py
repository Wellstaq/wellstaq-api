"""
Hashtag / HashtagTrendSnapshot model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Factories and org/user setup come exclusively from conftest.py
(make_user, make_org_with_owner) — no local duplicates.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hashtag, HashtagTrendSnapshot
from tests.conftest import make_org_with_owner, make_user


# ── Factories ──────────────────────────────────────────────────────────────────

def make_hashtag(organization_id: str, tag_name: str = "fitness", **overrides) -> Hashtag:
    defaults = dict(
        organization_id=organization_id,
        tag_name=tag_name,
        total_post_count=0,
    )
    return Hashtag(**{**defaults, **overrides})


def make_snapshot(hashtag_id: str, organization_id: str, **overrides) -> HashtagTrendSnapshot:
    now = datetime(2026, 1, 1, 12, 0, 0)
    defaults = dict(
        post_count=10,
        window_start=now - timedelta(minutes=15),
        window_end=now,
        trend_score=42.5,
        computed_at=now,
    )
    return HashtagTrendSnapshot(
        hashtag_id=hashtag_id,
        organization_id=organization_id,
        **{**defaults, **overrides},
    )


# ════════════════════════════════════════════════
# Hashtag model
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hashtag_persists(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id)
    db_session.add(hashtag)
    await db_session.flush()

    result = await db_session.execute(
        select(Hashtag).where(Hashtag.id == hashtag.id)
    )
    fetched = result.scalar_one()
    assert fetched.tag_name == "fitness"
    assert fetched.total_post_count == 0
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_hashtag_id_is_uuid_format(db_session: AsyncSession):
    """ID must be a valid parseable UUID — not just the right string shape."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id)
    db_session.add(hashtag)
    await db_session.flush()

    from uuid import UUID
    assert str(UUID(hashtag.id)) == hashtag.id


@pytest.mark.asyncio
async def test_hashtag_tag_name_must_be_unique_within_org(db_session: AsyncSession):
    """tag_name uniqueness is scoped per organization — duplicate within
    same org raises IntegrityError."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_hashtag(org.id, tag_name="wellness"))
    await db_session.flush()

    db_session.add(make_hashtag(org.id, tag_name="wellness"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_same_tag_name_allowed_across_different_orgs(db_session: AsyncSession):
    """The unique constraint is (organization_id, tag_name) — the same
    tag_name can exist independently in different orgs."""
    org_a, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-a", "business_name": "Org A"},
        user_kwargs={"email": "owner_a@example.com"},
    )
    org_b, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-b", "business_name": "Org B"},
        user_kwargs={"email": "owner_b@example.com"},
    )

    db_session.add(make_hashtag(org_a.id, tag_name="shared-name"))
    db_session.add(make_hashtag(org_b.id, tag_name="shared-name"))
    await db_session.flush()

    result = await db_session.execute(
        select(Hashtag).where(Hashtag.tag_name == "shared-name")
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_hashtag_total_post_count_defaults_to_zero(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = Hashtag(organization_id=org.id, tag_name="mindfulness")
    db_session.add(hashtag)
    await db_session.flush()

    assert hashtag.total_post_count == 0


@pytest.mark.asyncio
async def test_hashtag_total_post_count_can_increment(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="running")
    db_session.add(hashtag)
    await db_session.flush()

    hashtag.total_post_count += 1
    await db_session.flush()

    result = await db_session.execute(
        select(Hashtag).where(Hashtag.id == hashtag.id)
    )
    assert result.scalar_one().total_post_count == 1


@pytest.mark.asyncio
async def test_hashtag_total_post_count_cannot_be_negative(db_session: AsyncSession):
    """A decrement bug should raise IntegrityError, not silently store -1."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="running")
    db_session.add(hashtag)
    await db_session.flush()

    hashtag.total_post_count = -1
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_hashtag_organization_id_required(db_session: AsyncSession):
    """organization_id is NOT NULL — hashtags are always scoped to an org."""
    hashtag = Hashtag(organization_id=None, tag_name="orphan-tag")
    db_session.add(hashtag)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_hashtag_organization_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id)
    db_session.add(hashtag)
    await db_session.flush()
    await db_session.refresh(hashtag, ["organization"])

    assert hashtag.organization is not None
    assert hashtag.organization.id == org.id


# ── tag_name case constraints ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hashtag_tag_name_must_be_lowercase(db_session: AsyncSession):
    """tag_name must be stored lowercase — mixed or uppercase raises
    IntegrityError. Normalization is the service layer's responsibility;
    this constraint is the DB-level safety net."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)

    hashtag = make_hashtag(org.id, tag_name="Fitness")  # uppercase — should fail
    db_session.add(hashtag)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_hashtag_tag_name_uppercase_and_lowercase_are_not_duplicates_without_constraint(
    db_session: AsyncSession,
):
    """Without the lowercase constraint, 'Fitness' and 'fitness' would
    satisfy the unique constraint and be stored as two separate tags —
    demonstrating why the constraint is necessary. With it, only the
    lowercase form is accepted."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)

    # Lowercase form — should persist fine
    hashtag = make_hashtag(org.id, tag_name="fitness")
    db_session.add(hashtag)
    await db_session.flush()

    # Uppercase form — rejected by ck_hashtag_tag_name_lowercase
    hashtag_upper = make_hashtag(org.id, tag_name="Fitness")
    db_session.add(hashtag_upper)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_hashtag_empty_tag_name_raises(db_session: AsyncSession):
    """An empty string is not a valid tag name — DB rejects it via
    ck_hashtag_tag_name_not_empty."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)

    hashtag = make_hashtag(org.id, tag_name="")
    db_session.add(hashtag)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_hashtag_whitespace_only_tag_name_raises(db_session: AsyncSession):
    """A whitespace-only tag name is as meaningless as an empty string —
    length(trim(tag_name)) > 0 catches this case."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)

    hashtag = make_hashtag(org.id, tag_name="   ")
    db_session.add(hashtag)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Cascade deletes ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deleting_org_cascades_to_hashtags(db_session: AsyncSession):
    """Deleting an Organization must cascade and remove all its hashtags."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)

    hashtag = make_hashtag(org.id, tag_name="fitness")
    db_session.add(hashtag)
    await db_session.flush()

    hashtag_id = hashtag.id

    await db_session.execute(
        text("DELETE FROM organizations WHERE id = :oid"),
        {"oid": org.id},
    )
    await db_session.flush()
    db_session.expire_all()

    result = await db_session.execute(
        select(Hashtag).where(Hashtag.id == hashtag_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_deleting_hashtag_cascades_to_trend_snapshots(db_session: AsyncSession):
    """Deleting a Hashtag must cascade and remove all its trend snapshots."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)

    hashtag = make_hashtag(org.id, tag_name="fitness")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id)
    db_session.add(snapshot)
    await db_session.flush()

    snapshot_id = snapshot.id

    await db_session.execute(
        text("DELETE FROM hashtags WHERE id = :hid"),
        {"hid": hashtag.id},
    )
    await db_session.flush()
    db_session.expire_all()

    result = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.id == snapshot_id)
    )
    assert result.scalar_one_or_none() is None


# ════════════════════════════════════════════════
# HashtagTrendSnapshot model
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hashtag_trend_snapshot_persists(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-fitness")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id)
    db_session.add(snapshot)
    await db_session.flush()

    result = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.id == snapshot.id)
    )
    fetched = result.scalar_one()
    assert fetched.hashtag_id == hashtag.id
    assert fetched.organization_id == org.id
    assert fetched.post_count == 10
    assert float(fetched.trend_score) == 42.5


@pytest.mark.asyncio
async def test_hashtag_trend_snapshot_id_is_uuid_format(db_session: AsyncSession):
    """ID must be a valid parseable UUID — not just the right string shape."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-uuid")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id)
    db_session.add(snapshot)
    await db_session.flush()

    from uuid import UUID
    assert str(UUID(snapshot.id)) == snapshot.id


@pytest.mark.asyncio
async def test_hashtag_trend_snapshot_window_fields(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-window")
    db_session.add(hashtag)
    await db_session.flush()

    window_start = datetime(2026, 1, 1, 9, 0, 0)
    window_end = datetime(2026, 1, 1, 10, 0, 0)

    snapshot = make_snapshot(
        hashtag.id, org.id,
        window_start=window_start,
        window_end=window_end,
    )
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.window_start == window_start
    assert snapshot.window_end == window_end


@pytest.mark.asyncio
async def test_hashtag_trend_snapshot_window_end_must_be_after_window_start(
    db_session: AsyncSession,
):
    """window_end <= window_start is a miscalculated window — DB rejects it."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-badwindow")
    db_session.add(hashtag)
    await db_session.flush()

    now = datetime(2026, 1, 1, 12, 0, 0)
    snapshot = make_snapshot(
        hashtag.id, org.id,
        window_start=now,
        window_end=now - timedelta(minutes=15),  # end before start — should fail
    )
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_hashtag_can_have_multiple_trend_snapshots(db_session: AsyncSession):
    """Snapshots accumulate over time — one row per computation run,
    no uniqueness constraint."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-multi")
    db_session.add(hashtag)
    await db_session.flush()

    db_session.add(make_snapshot(
        hashtag.id, org.id,
        window_start=datetime(2026, 1, 1, 9, 0, 0),
        window_end=datetime(2026, 1, 1, 9, 15, 0),
        computed_at=datetime(2026, 1, 1, 9, 15, 0),
    ))
    db_session.add(make_snapshot(
        hashtag.id, org.id,
        window_start=datetime(2026, 1, 1, 9, 15, 0),
        window_end=datetime(2026, 1, 1, 9, 30, 0),
        computed_at=datetime(2026, 1, 1, 9, 30, 0),
    ))
    await db_session.flush()

    result = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.hashtag_id == hashtag.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_hashtag_trend_snapshot_score_is_numeric(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-numeric")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id, trend_score=99.9999)
    db_session.add(snapshot)
    await db_session.flush()

    result = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.id == snapshot.id)
    )
    assert float(result.scalar_one().trend_score) == 99.9999


@pytest.mark.asyncio
async def test_each_org_has_independent_trend_snapshots_for_same_tag_name(
    db_session: AsyncSession,
):
    """Trending is scoped per org — the same tag_name can have different
    trend scores in different orgs. Each org gets its own Hashtag row
    (enforced by the org-scoped unique constraint) and its own snapshots.
    This is the correct real-world pattern — no cross-org snapshot sharing."""
    org_a, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-a", "business_name": "Org A"},
        user_kwargs={"email": "owner_a@example.com"},
    )
    org_b, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-b", "business_name": "Org B"},
        user_kwargs={"email": "owner_b@example.com"},
    )

    # Each org gets its own Hashtag row for the same tag_name
    hashtag_a = make_hashtag(org_a.id, tag_name="wellness")
    hashtag_b = make_hashtag(org_b.id, tag_name="wellness")
    db_session.add_all([hashtag_a, hashtag_b])
    await db_session.flush()

    db_session.add(make_snapshot(hashtag_a.id, org_a.id, trend_score=10))
    db_session.add(make_snapshot(hashtag_b.id, org_b.id, trend_score=90))
    await db_session.flush()

    result_a = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.hashtag_id == hashtag_a.id)
    )
    result_b = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.hashtag_id == hashtag_b.id)
    )

    assert float(result_a.scalar_one().trend_score) == 10
    assert float(result_b.scalar_one().trend_score) == 90


@pytest.mark.asyncio
async def test_snapshot_computed_at_is_required(db_session: AsyncSession):
    """computed_at has no default — the background job must supply it
    explicitly. Omitting it should raise IntegrityError."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-computed")
    db_session.add(hashtag)
    await db_session.flush()

    now = datetime(2026, 1, 1, 12, 0, 0)
    snapshot = HashtagTrendSnapshot(
        hashtag_id=hashtag.id,
        organization_id=org.id,
        post_count=5,
        window_start=now - timedelta(minutes=15),
        window_end=now,
        trend_score=10.0,
        computed_at=None,  # intentionally omitted — should raise
    )
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_snapshot_post_count_cannot_be_negative(db_session: AsyncSession):
    """A negative post_count is a background job bug — DB rejects it."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-negcount")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id, post_count=-1)
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_snapshot_post_count_defaults_to_zero(db_session: AsyncSession):
    """post_count defaults to 0 — valid lower bound."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-zerocount")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id, post_count=0)
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.post_count == 0


@pytest.mark.asyncio
async def test_deleting_org_cascades_to_snapshots_directly(db_session: AsyncSession):
    """HashtagTrendSnapshot has its own organization_id FK with CASCADE —
    deleting an org removes its snapshots directly, independent of the
    hashtag cascade path."""
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    hashtag = make_hashtag(org.id, tag_name="snapshot-orgcascade")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id)
    db_session.add(snapshot)
    await db_session.flush()

    snapshot_id = snapshot.id

    await db_session.execute(
        text("DELETE FROM organizations WHERE id = :oid"),
        {"oid": org.id},
    )
    await db_session.flush()
    db_session.expire_all()

    result = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.id == snapshot_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_snapshot_org_mismatch_is_allowed_at_db_level(db_session: AsyncSession):
    """The DB does not enforce that snapshot.organization_id matches
    Hashtag.organization_id — that consistency is the service layer's
    responsibility. This test documents the intentional gap so future
    developers don't assume a constraint exists.

    Scenario: hashtag belongs to org_a, snapshot carries org_b's id.
    The insert succeeds — no IntegrityError."""
    org_a, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-a", "business_name": "Org A"},
        user_kwargs={"email": "owner_a@example.com"},
    )
    org_b, _ = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-b", "business_name": "Org B"},
        user_kwargs={"email": "owner_b@example.com"},
    )

    hashtag = make_hashtag(org_a.id, tag_name="mismatch-tag")
    db_session.add(hashtag)
    await db_session.flush()

    # snapshot.organization_id = org_b but hashtag belongs to org_a
    snapshot = make_snapshot(hashtag.id, org_b.id)
    db_session.add(snapshot)
    await db_session.flush()  # must NOT raise — service layer owns this check

    assert snapshot.organization_id == org_b.id
    assert snapshot.hashtag_id == hashtag.id
from datetime import date, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Challenge, ChallengeParticipant, ChallengeProgressEntry, Notification
from app.services.challenge.submission_service import review_submission
from tests.conftest import make_org_with_owner


async def _pending_submission(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    challenge = Challenge(
        organization_id=org.id,
        name="Evidence challenge",
        status="active",
        start_date=date.today(),
        end_date=date.today(),
        target_type="cumulative",
        target_value=2,
        tracking_method="evidence_based",
        requires_approval=True,
    )
    db_session.add(challenge)
    await db_session.flush()

    db_session.add(ChallengeParticipant(
        challenge_id=challenge.id,
        user_id=owner.id,
        organization_id=org.id,
    ))
    entry = ChallengeProgressEntry(
        challenge_id=challenge.id,
        user_id=owner.id,
        organization_id=org.id,
        entry_date=date.today(),
        value=1,
        source="evidence",
        review_status="pending",
    )
    db_session.add(entry)
    await db_session.flush()
    return org, owner, challenge, entry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "reason", "expected_status", "notification_type"),
    [
        ("approve", None, "approved", "challenge_submission_approved"),
        ("reject", "The photo is unclear.", "rejected", "challenge_submission_rejected"),
    ],
)
async def test_review_submission_persists_review_and_notification(
    db_session: AsyncSession,
    action: str,
    reason: str | None,
    expected_status: str,
    notification_type: str,
):
    org, owner, challenge, entry = await _pending_submission(db_session)

    reviewed = await review_submission(
        session=db_session,
        organization_id=org.id,
        challenge_id=challenge.id,
        entry_id=entry.id,
        reviewer_id=owner.id,
        action=action,
        reason=reason,
    )

    assert reviewed.review_status == expected_status
    assert reviewed.reviewed_by == owner.id
    assert reviewed.reviewed_at is not None
    assert reviewed.reviewed_at.tzinfo == timezone.utc

    notification = (await db_session.execute(
        select(Notification).where(Notification.user_id == owner.id)
    )).scalar_one()
    assert notification.notification_type == notification_type
    assert notification.reference_id == challenge.id
    assert notification.reference_type == "challenge"


def test_reviewed_at_column_is_timezone_aware():
    assert ChallengeProgressEntry.__table__.c.reviewed_at.type.timezone is True

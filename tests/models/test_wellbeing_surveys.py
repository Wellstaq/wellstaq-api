"""
WellbeingSurvey models tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Key patterns enforced:
  - User.branch_id is NOT NULL — every make_user() call passes
    branch_id=owner.branch_id (the HQ branch auto-created by
    make_org_with_owner).
  - WellbeingPulseSnapshot supports both org-wide (branch_id=None)
    and branch-scoped (branch_id set) snapshots. Tests verify the
    unique constraint allows one of each per window.
  - Response values must be 1-5, question keys must be valid.
  - Survey windows cannot overlap (enforced by unique constraint on
    org_id + window_start, plus application logic not tested here).
"""

import pytest
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    WellbeingSurveyConfig,
    WellbeingSurveyWindow,
    WellbeingSurveyResponse,
    WellbeingPulseSnapshot,
    Organization,
    User,
    Branch,
)
from app.models.wellbeing_survey import (
    PULSE_QUESTION_KEYS,
    SURVEY_CADENCES,
    STATUS_LOOKING_GOOD,
    STATUS_NEEDS_ATTENTION,
    STATUS_PRIORITY_FOCUS,
    score_to_status,
)
from tests.conftest import make_org_with_owner, make_user_with_role


# ── Helpers ───────────────────────────────────────────────────────────────────

async def setup_org_with_config(db_session: AsyncSession, slug: str = "wellbeing-test-org"):
    """Create an org + owner + active survey config."""
    org, owner = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs=dict(slug=slug),
        user_kwargs=dict(email=f"owner-{slug}@example.com"),
    )
    config = WellbeingSurveyConfig(
        organization_id=org.id,
        cadence="monthly",
        is_active=True,
    )
    db_session.add(config)
    await db_session.flush()
    return org, owner, config


async def create_survey_window(
    db_session: AsyncSession,
    organization_id: str,
    window_start: date,
    window_end: date,
    is_closed: bool = False,
) -> WellbeingSurveyWindow:
    """Create a survey window."""
    window = WellbeingSurveyWindow(
        organization_id=organization_id,
        window_start=window_start,
        window_end=window_end,
        is_closed=is_closed,
    )
    db_session.add(window)
    await db_session.flush()
    return window


async def create_and_persist_user(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    email: str,
    role_name: str = "member",
) -> User:
    """Create a user with role, add to session, flush, and return."""
    user = await make_user_with_role(
        db_session,
        organization_id=organization_id,
        email=email,
        role_name=role_name,
        branch_id=branch_id,
    )
    db_session.add(user)
    await db_session.flush()
    return user


# ── score_to_status function tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_to_status_green_threshold(db_session: AsyncSession):
    """75+ percent returns 'Looking good'."""
    assert score_to_status(75.0) == STATUS_LOOKING_GOOD
    assert score_to_status(80.0) == STATUS_LOOKING_GOOD
    assert score_to_status(100.0) == STATUS_LOOKING_GOOD


@pytest.mark.asyncio
async def test_score_to_status_amber_threshold(db_session: AsyncSession):
    """60-74 percent returns 'Needs attention'."""
    assert score_to_status(60.0) == STATUS_NEEDS_ATTENTION
    assert score_to_status(67.5) == STATUS_NEEDS_ATTENTION
    assert score_to_status(74.9) == STATUS_NEEDS_ATTENTION


@pytest.mark.asyncio
async def test_score_to_status_red_threshold(db_session: AsyncSession):
    """<60 percent returns 'Priority focus'."""
    assert score_to_status(59.9) == STATUS_PRIORITY_FOCUS
    assert score_to_status(40.0) == STATUS_PRIORITY_FOCUS
    assert score_to_status(0.0) == STATUS_PRIORITY_FOCUS


# ── WellbeingSurveyConfig model tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_survey_config_persists(db_session: AsyncSession):
    org, owner, config = await setup_org_with_config(db_session)

    result = await db_session.execute(
        select(WellbeingSurveyConfig).where(WellbeingSurveyConfig.id == config.id)
    )
    fetched = result.scalar_one()

    assert fetched.organization_id == org.id
    assert fetched.cadence == "monthly"
    assert fetched.is_active is True


@pytest.mark.asyncio
async def test_survey_config_id_is_uuid_format(db_session: AsyncSession):
    org, owner, config = await setup_org_with_config(db_session)

    parts = config.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_survey_config_organization_unique(db_session: AsyncSession):
    """Only one config per organization."""
    org, owner, config = await setup_org_with_config(db_session)

    duplicate = WellbeingSurveyConfig(
        organization_id=org.id,
        cadence="weekly",
        is_active=True,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_config_invalid_cadence_raises(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    config = WellbeingSurveyConfig(
        organization_id=org.id,
        cadence="daily",  # Invalid
        is_active=True,
    )
    db_session.add(config)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_config_valid_cadences(db_session: AsyncSession):
    """All defined cadences are valid."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    for cadence in SURVEY_CADENCES:
        config = WellbeingSurveyConfig(
            organization_id=org.id,
            cadence=cadence,
            is_active=True,
        )
        db_session.add(config)
        await db_session.flush()
        # Clean up for next iteration
        await db_session.delete(config)
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_config_deactivation(db_session: AsyncSession):
    org, owner, config = await setup_org_with_config(db_session)

    config.is_active = False
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingSurveyConfig).where(WellbeingSurveyConfig.id == config.id)
    )
    fetched = result.scalar_one()
    assert fetched.is_active is False


@pytest.mark.asyncio
async def test_survey_config_organization_relationship(db_session: AsyncSession):
    org, owner, config = await setup_org_with_config(db_session)
    await db_session.refresh(config, ["organization"])

    assert config.organization is not None
    assert config.organization.id == org.id


@pytest.mark.asyncio
async def test_organization_delete_cascades_to_config(db_session: AsyncSession):
    org, owner, config = await setup_org_with_config(db_session)
    config_id = config.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingSurveyConfig).where(WellbeingSurveyConfig.id == config_id)
    )
    assert result.scalar_one_or_none() is None


# ── WellbeingSurveyWindow model tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_survey_window_persists(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)

    window = await create_survey_window(db_session, org.id, start_date, end_date)

    result = await db_session.execute(
        select(WellbeingSurveyWindow).where(WellbeingSurveyWindow.id == window.id)
    )
    fetched = result.scalar_one()

    assert fetched.organization_id == org.id
    assert fetched.window_start == start_date
    assert fetched.window_end == end_date
    assert fetched.is_closed is False


@pytest.mark.asyncio
async def test_survey_window_id_is_uuid_format(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)

    window = await create_survey_window(db_session, org.id, start_date, end_date)

    parts = window.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_survey_window_invalid_date_range_raises(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 7)
    end_date = date(2025, 1, 1)  # End before start

    window = WellbeingSurveyWindow(
        organization_id=org.id,
        window_start=start_date,
        window_end=end_date,
    )
    db_session.add(window)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_window_unique_org_start_constraint(db_session: AsyncSession):
    """Cannot create two windows starting on the same date for same org."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)

    await create_survey_window(db_session, org.id, start_date, date(2025, 1, 7))

    duplicate = WellbeingSurveyWindow(
        organization_id=org.id,
        window_start=start_date,
        window_end=date(2025, 1, 14),
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_window_different_orgs_can_share_start_date(db_session: AsyncSession):
    """Different organizations can have windows starting on the same date."""
    org1, owner1, _ = await setup_org_with_config(db_session, slug="org-one")
    org2, owner2, _ = await setup_org_with_config(db_session, slug="org-two")
    start_date = date(2025, 1, 1)

    window1 = await create_survey_window(db_session, org1.id, start_date, date(2025, 1, 7))
    window2 = await create_survey_window(db_session, org2.id, start_date, date(2025, 1, 7))

    assert window1.id is not None
    assert window2.id is not None


@pytest.mark.asyncio
async def test_survey_window_can_be_closed(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)

    window = await create_survey_window(db_session, org.id, start_date, end_date, is_closed=True)

    assert window.is_closed is True


@pytest.mark.asyncio
async def test_survey_window_organization_relationship(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)

    window = await create_survey_window(db_session, org.id, start_date, end_date)
    await db_session.refresh(window, ["organization"])

    assert window.organization is not None
    assert window.organization.id == org.id


@pytest.mark.asyncio
async def test_organization_delete_cascades_to_windows(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)

    window = await create_survey_window(db_session, org.id, start_date, end_date)
    window_id = window.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingSurveyWindow).where(WellbeingSurveyWindow.id == window_id)
    )
    assert result.scalar_one_or_none() is None


# ── WellbeingSurveyResponse model tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_survey_response_persists(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    response = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=4,
    )
    db_session.add(response)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingSurveyResponse).where(WellbeingSurveyResponse.id == response.id)
    )
    fetched = result.scalar_one()

    assert fetched.window_id == window.id
    assert fetched.user_id == user.id
    assert fetched.organization_id == org.id
    assert fetched.question_key == "stress_manageability"
    assert fetched.response_value == 4


@pytest.mark.asyncio
async def test_survey_response_id_is_uuid_format(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    response = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=4,
    )
    db_session.add(response)
    await db_session.flush()

    parts = response.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_survey_response_valid_values(db_session: AsyncSession):
    """Response values 1-5 are valid."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    for value in [1, 2, 3, 4, 5]:
        response = WellbeingSurveyResponse(
            window_id=window.id,
            user_id=user.id,
            organization_id=org.id,
            question_key="stress_manageability",
            response_value=value,
        )
        db_session.add(response)
        await db_session.flush()
        assert response.response_value == value
        # Clean up for next iteration
        await db_session.delete(response)
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_response_value_below_min_raises(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    response = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=0,  # Invalid
    )
    db_session.add(response)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_response_value_above_max_raises(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    response = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=6,  # Invalid
    )
    db_session.add(response)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_response_valid_question_keys(db_session: AsyncSession):
    """All defined question keys are valid."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    for question_key in PULSE_QUESTION_KEYS:
        response = WellbeingSurveyResponse(
            window_id=window.id,
            user_id=user.id,
            organization_id=org.id,
            question_key=question_key,
            response_value=3,
        )
        db_session.add(response)
        await db_session.flush()
        assert response.question_key == question_key
        # Clean up for next iteration
        await db_session.delete(response)
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_response_invalid_question_key_raises(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    response = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="invalid_question",  # Invalid
        response_value=3,
    )
    db_session.add(response)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_response_unique_window_user_question(db_session: AsyncSession):
    """Cannot create duplicate responses for same user/question/window."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    response1 = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=4,
    )
    db_session.add(response1)
    await db_session.flush()

    response2 = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=5,  # Different value doesn't matter
    )
    db_session.add(response2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_survey_response_different_users_same_question_allowed(db_session: AsyncSession):
    """Different users can answer the same question in the same window."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user1 = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="user1@example.com",
    )
    user2 = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="user2@example.com",
    )

    response1 = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user1.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=4,
    )
    db_session.add(response1)
    await db_session.flush()

    response2 = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user2.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=2,
    )
    db_session.add(response2)
    await db_session.flush()

    assert response1.id is not None
    assert response2.id is not None


@pytest.mark.asyncio
async def test_survey_response_user_can_answer_different_questions(db_session: AsyncSession):
    """Same user can answer different questions in the same window."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    responses = []
    for question_key in PULSE_QUESTION_KEYS:
        response = WellbeingSurveyResponse(
            window_id=window.id,
            user_id=user.id,
            organization_id=org.id,
            question_key=question_key,
            response_value=3,
        )
        db_session.add(response)
        await db_session.flush()
        responses.append(response)

    assert len(responses) == len(PULSE_QUESTION_KEYS)


@pytest.mark.asyncio
async def test_survey_response_window_relationship(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    response = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=4,
    )
    db_session.add(response)
    await db_session.flush()
    await db_session.refresh(response, ["window"])

    assert response.window is not None
    assert response.window.id == window.id


@pytest.mark.asyncio
async def test_survey_response_user_relationship(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    response = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=4,
    )
    db_session.add(response)
    await db_session.flush()
    await db_session.refresh(response, ["user"])

    assert response.user is not None
    assert response.user.id == user.id


@pytest.mark.asyncio
async def test_window_delete_cascades_to_responses(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    user = await create_and_persist_user(
        db_session,
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="respondent@example.com",
    )

    response = WellbeingSurveyResponse(
        window_id=window.id,
        user_id=user.id,
        organization_id=org.id,
        question_key="stress_manageability",
        response_value=4,
    )
    db_session.add(response)
    await db_session.flush()
    response_id = response.id

    await db_session.delete(window)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingSurveyResponse).where(WellbeingSurveyResponse.id == response_id)
    )
    assert result.scalar_one_or_none() is None


# ── WellbeingPulseSnapshot model tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_org_wide_persists(db_session: AsyncSession):
    """Org-wide snapshot (branch_id=None) is valid."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    snapshot = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=None,  # Org-wide
        stress_manageability_pct=Decimal("75.00"),
        energy_recovery_pct=Decimal("60.00"),
        connection_belonging_pct=Decimal("80.00"),
        workload_sustainability_pct=Decimal("50.00"),
        workplace_comfort_pct=Decimal("90.00"),
        priority_support_pct=Decimal("10.00"),
        needs_attention_pct=Decimal("20.00"),
        doing_well_pct=Decimal("70.00"),
        respondent_count=0,
    )
    db_session.add(snapshot)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingPulseSnapshot).where(WellbeingPulseSnapshot.id == snapshot.id)
    )
    fetched = result.scalar_one()

    assert fetched.window_id == window.id
    assert fetched.organization_id == org.id
    assert fetched.branch_id is None
    assert fetched.respondent_count == 0


@pytest.mark.asyncio
async def test_snapshot_branch_scoped_persists(db_session: AsyncSession):
    """Branch-scoped snapshot (branch_id set) is valid."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    # Create a branch
    branch = Branch(
        name="Test Branch",
        organization_id=org.id,
    )
    db_session.add(branch)
    await db_session.flush()

    snapshot = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=branch.id,  # Branch-scoped
        stress_manageability_pct=Decimal("75.00"),
        energy_recovery_pct=Decimal("60.00"),
        connection_belonging_pct=Decimal("80.00"),
        workload_sustainability_pct=Decimal("50.00"),
        workplace_comfort_pct=Decimal("90.00"),
        priority_support_pct=Decimal("10.00"),
        needs_attention_pct=Decimal("20.00"),
        doing_well_pct=Decimal("70.00"),
        respondent_count=5,
    )
    db_session.add(snapshot)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingPulseSnapshot).where(WellbeingPulseSnapshot.id == snapshot.id)
    )
    fetched = result.scalar_one()

    assert fetched.window_id == window.id
    assert fetched.organization_id == org.id
    assert fetched.branch_id == branch.id
    assert fetched.respondent_count == 5


@pytest.mark.asyncio
async def test_snapshot_id_is_uuid_format(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    snapshot = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=None,
        respondent_count=0,
    )
    db_session.add(snapshot)
    await db_session.flush()

    parts = snapshot.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_snapshot_unique_window_branch_constraint(db_session: AsyncSession):
    """Cannot create two snapshots for the same window+branch combination.
    
    NOTE: SQLite allows multiple NULL values in unique constraints, so this
    test uses a branch-scoped snapshot (branch_id set) to properly test
    the unique constraint enforcement.
    """
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    # Create a branch
    branch = Branch(
        name="Test Branch",
        organization_id=org.id,
    )
    db_session.add(branch)
    await db_session.flush()

    # Create first branch-scoped snapshot
    snapshot1 = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=branch.id,
        respondent_count=0,
    )
    db_session.add(snapshot1)
    await db_session.flush()

    # Try to create duplicate branch-scoped snapshot
    snapshot2 = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=branch.id,
        respondent_count=0,
    )
    db_session.add(snapshot2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_snapshot_one_org_wide_one_branch_scoped_allowed(db_session: AsyncSession):
    """Can have one org-wide AND one branch-scoped snapshot per window."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    # Create a branch
    branch = Branch(
        name="Test Branch",
        organization_id=org.id,
    )
    db_session.add(branch)
    await db_session.flush()

    # Org-wide snapshot
    snapshot1 = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=None,
        respondent_count=0,
    )
    db_session.add(snapshot1)
    await db_session.flush()

    # Branch-scoped snapshot
    snapshot2 = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=branch.id,
        respondent_count=0,
    )
    db_session.add(snapshot2)
    await db_session.flush()

    assert snapshot1.id is not None
    assert snapshot2.id is not None


@pytest.mark.asyncio
async def test_snapshot_different_branches_can_each_have_snapshots(db_session: AsyncSession):
    """Different branches can each have their own snapshot."""
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    # Create branches
    branch1 = Branch(name="Branch 1", organization_id=org.id)
    branch2 = Branch(name="Branch 2", organization_id=org.id)
    db_session.add_all([branch1, branch2])
    await db_session.flush()

    snapshot1 = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=branch1.id,
        respondent_count=0,
    )
    snapshot2 = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=branch2.id,
        respondent_count=0,
    )
    db_session.add_all([snapshot1, snapshot2])
    await db_session.flush()

    assert snapshot1.id is not None
    assert snapshot2.id is not None


@pytest.mark.asyncio
async def test_snapshot_computed_at_auto_set(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    snapshot = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=None,
        respondent_count=0,
    )
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.computed_at is not None
    assert snapshot.computed_at.tzinfo is not None  # Must be timezone-aware


@pytest.mark.asyncio
async def test_snapshot_window_relationship(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    snapshot = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=None,
        respondent_count=0,
    )
    db_session.add(snapshot)
    await db_session.flush()
    await db_session.refresh(snapshot, ["window"])

    assert snapshot.window is not None
    assert snapshot.window.id == window.id


@pytest.mark.asyncio
async def test_snapshot_organization_relationship(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    snapshot = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=None,
        respondent_count=0,
    )
    db_session.add(snapshot)
    await db_session.flush()
    await db_session.refresh(snapshot, ["organization"])

    assert snapshot.organization is not None
    assert snapshot.organization.id == org.id


@pytest.mark.asyncio
async def test_window_delete_cascades_to_snapshots(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    snapshot = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=None,
        respondent_count=0,
    )
    db_session.add(snapshot)
    await db_session.flush()
    snapshot_id = snapshot.id

    await db_session.delete(window)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingPulseSnapshot).where(WellbeingPulseSnapshot.id == snapshot_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_organization_delete_cascades_to_snapshots(db_session: AsyncSession):
    org, owner, _ = await setup_org_with_config(db_session)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 7)
    window = await create_survey_window(db_session, org.id, start_date, end_date)

    snapshot = WellbeingPulseSnapshot(
        window_id=window.id,
        organization_id=org.id,
        branch_id=None,
        respondent_count=0,
    )
    db_session.add(snapshot)
    await db_session.flush()
    snapshot_id = snapshot.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(WellbeingPulseSnapshot).where(WellbeingPulseSnapshot.id == snapshot_id)
    )
    assert result.scalar_one_or_none() is None

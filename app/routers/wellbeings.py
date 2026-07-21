"""
app/routers/wellbeings.py

Access model — four tiers:
  Lookups (dimensions, challenge categories) — read-only, any authenticated
    user, no org scoping (global data).
  Org challenge opt-in — org.settings.update, always org-wide.
  Baseline / health assessment — fully personal, self-only, no admin path.
  Wellbeing score — personal (self, full detail) vs aggregate (branch/org
    AVERAGE only, wellbeing.view_team, suppressed below the anonymity
    threshold — see app/services/wellbeing/_helpers.py).

Routes:
  GET  /wellbeing/dimensions
  GET  /wellbeing/challenges

  POST   /organizations/{org_id}/wellbeing/opted-in-challenges
  DELETE /organizations/{org_id}/wellbeing/opted-in-challenges/{wellbeing_challenge_id}
  GET    /organizations/{org_id}/wellbeing/opted-in-challenges

  POST /wellbeing/baselines
  GET  /wellbeing/baselines/me

  POST /wellbeing/assessments
  GET  /wellbeing/assessments/me

  GET /wellbeing/scores/me
  GET /organizations/{org_id}/branches/{branch_id}/wellbeing/scores/aggregate
  GET /organizations/{org_id}/wellbeing/scores/aggregate
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_permission, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.wellbeing import (
    AssessmentListResponse,
    AssessmentResponse,
    AssessmentSubmitRequest,
    BaselineListResponse,
    BaselineResponse,
    BaselineSubmitRequest,
    OrgChallengeOptInListResponse,
    OrgChallengeOptInRequest,
    OrgChallengeOptInResponse,
    WellbeingAggregateResponse,
    WellbeingChallengeListResponse,
    WellbeingChallengeResponse,
    WellbeingDimensionListResponse,
    WellbeingDimensionResponse,
    WellbeingScoreListResponse,
    WellbeingScoreResponse,
)
from app.services.wellbeing import (
    get_branch_aggregate_score,
    get_my_assessments,
    get_my_baselines,
    get_my_scores,
    get_org_aggregate_score,
    list_dimensions,
    list_opted_in_challenges,
    list_wellbeing_challenges,
    opt_in_to_challenge,
    opt_out_of_challenge,
    submit_assessment,
    submit_baseline,
)

router = APIRouter(tags=["wellbeing"])


# ── Lookups ──────────────────────────────────────────────────────────────────

@router.get("/wellbeing/dimensions", response_model=WellbeingDimensionListResponse, status_code=status.HTTP_200_OK)
async def list_dimensions_route(
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> WellbeingDimensionListResponse:
    async with request_guard(db, "/wellbeing/dimensions"):
        dimensions = await list_dimensions(session=db)
        return WellbeingDimensionListResponse(items=[WellbeingDimensionResponse.model_validate(d) for d in dimensions])


@router.get("/wellbeing/challenges", response_model=WellbeingChallengeListResponse, status_code=status.HTTP_200_OK)
async def list_wellbeing_challenges_route(
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> WellbeingChallengeListResponse:
    async with request_guard(db, "/wellbeing/challenges"):
        challenges = await list_wellbeing_challenges(session=db)
        return WellbeingChallengeListResponse(items=[WellbeingChallengeResponse.model_validate(c) for c in challenges])


# ── Org challenge opt-in ─────────────────────────────────────────────────────

@router.post(
    "/organizations/{org_id}/wellbeing/opted-in-challenges",
    response_model=OrgChallengeOptInResponse, status_code=status.HTTP_201_CREATED,
)
async def opt_in_route(
    org_id: str, body: OrgChallengeOptInRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("org.settings.update")),
) -> OrgChallengeOptInResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/wellbeing/opted-in-challenges"):
        opt_in = await opt_in_to_challenge(session=db, organization_id=org_id, wellbeing_challenge_id=body.wellbeing_challenge_id)
        await db.commit()
        return OrgChallengeOptInResponse.model_validate(opt_in)


@router.delete(
    "/organizations/{org_id}/wellbeing/opted-in-challenges/{wellbeing_challenge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def opt_out_route(
    org_id: str, wellbeing_challenge_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("org.settings.update")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/wellbeing/opted-in-challenges/{wellbeing_challenge_id}"):
        await opt_out_of_challenge(session=db, organization_id=org_id, wellbeing_challenge_id=wellbeing_challenge_id)
        await db.commit()


@router.get(
    "/organizations/{org_id}/wellbeing/opted-in-challenges",
    response_model=OrgChallengeOptInListResponse, status_code=status.HTTP_200_OK,
)
async def list_opted_in_route(
    org_id: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> OrgChallengeOptInListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/wellbeing/opted-in-challenges"):
        opt_ins = await list_opted_in_challenges(session=db, organization_id=org_id)
        return OrgChallengeOptInListResponse(items=[OrgChallengeOptInResponse.model_validate(o) for o in opt_ins])


# ── Personal baseline ────────────────────────────────────────────────────────

@router.post("/wellbeing/baselines", response_model=BaselineResponse, status_code=status.HTTP_201_CREATED)
async def submit_baseline_route(
    body: BaselineSubmitRequest,
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> BaselineResponse:
    async with request_guard(db, "/wellbeing/baselines"):
        baseline = await submit_baseline(
            session=db, organization_id=current_user.organization_id, user_id=current_user.user_id,
            dimension=body.dimension, level=body.level, reason=body.reason,
        )
        await db.commit()
        return BaselineResponse.model_validate(baseline)


@router.get("/wellbeing/baselines/me", response_model=BaselineListResponse, status_code=status.HTTP_200_OK)
async def my_baselines_route(
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> BaselineListResponse:
    async with request_guard(db, "/wellbeing/baselines/me"):
        baselines = await get_my_baselines(session=db, user_id=current_user.user_id)
        return BaselineListResponse(items=[BaselineResponse.model_validate(b) for b in baselines])


# ── Personal health assessment ───────────────────────────────────────────────

@router.post("/wellbeing/assessments", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
async def submit_assessment_route(
    body: AssessmentSubmitRequest,
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> AssessmentResponse:
    async with request_guard(db, "/wellbeing/assessments"):
        assessment = await submit_assessment(
            session=db, organization_id=current_user.organization_id, user_id=current_user.user_id,
            category_id=body.category_id, risk_score=body.risk_score, status_=body.status,
        )
        await db.commit()
        return AssessmentResponse.model_validate(assessment)


@router.get("/wellbeing/assessments/me", response_model=AssessmentListResponse, status_code=status.HTTP_200_OK)
async def my_assessments_route(
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> AssessmentListResponse:
    async with request_guard(db, "/wellbeing/assessments/me"):
        assessments = await get_my_assessments(session=db, user_id=current_user.user_id)
        return AssessmentListResponse(items=[AssessmentResponse.model_validate(a) for a in assessments])


# ── Wellbeing score ──────────────────────────────────────────────────────────

@router.get("/wellbeing/scores/me", response_model=WellbeingScoreListResponse, status_code=status.HTTP_200_OK)
async def my_scores_route(
    period: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> WellbeingScoreListResponse:
    async with request_guard(db, "/wellbeing/scores/me"):
        scores = await get_my_scores(session=db, user_id=current_user.user_id, period=period)
        return WellbeingScoreListResponse(items=[WellbeingScoreResponse.model_validate(s) for s in scores])

@router.get(
    "/organizations/{org_id}/branches/{branch_id}/wellbeing/scores/aggregate",
    response_model=WellbeingAggregateResponse, status_code=status.HTTP_200_OK,
    description=(
        "Average only, never individual scores. Suppressed if fewer than "
        "the anonymity threshold have a score for this dimension/period. "
        "Requires wellbeing.view_team scoped to this branch (or org-wide)."
    ),
)
async def branch_aggregate_score_route(
    org_id: str, branch_id: str, dimension_id: str = Query(...), period: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> WellbeingAggregateResponse:
    require_same_organization(org_id, current_user)

    from app.auth.dependencies import require_scoped_permission
    await require_scoped_permission(
        db=db, current_user=current_user, permission="wellbeing.view_team", target_branch_id=branch_id,
    )

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/wellbeing/scores/aggregate"):
        average, count, suppressed = await get_branch_aggregate_score(
            session=db, organization_id=org_id, branch_id=branch_id, dimension_id=dimension_id, period=period,
        )
        return WellbeingAggregateResponse(
            dimension_id=dimension_id, period=period, average_score=average, participant_count=count, suppressed=suppressed,
        )


@router.get(
    "/organizations/{org_id}/wellbeing/scores/aggregate",
    response_model=WellbeingAggregateResponse, status_code=status.HTTP_200_OK,
    description=(
        "Same anonymity-threshold rule as the branch aggregate, applied "
        "org-wide. Requires wellbeing.view_team held ORG-WIDE — a "
        "branch-scoped grant is not sufficient here, since this endpoint "
        "returns a figure blended across every branch, not just the one "
        "the grant covers."
    ),
)
async def org_aggregate_score_route(
    org_id: str, dimension_id: str = Query(...), period: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> WellbeingAggregateResponse:
    require_same_organization(org_id, current_user)

    from app.auth.dependencies import require_scoped_permission
    await require_scoped_permission(
        db=db, current_user=current_user, permission="wellbeing.view_team", target_branch_id=None,
    )

    async with request_guard(db, "/organizations/{org_id}/wellbeing/scores/aggregate"):
        average, count, suppressed = await get_org_aggregate_score(
            session=db, organization_id=org_id, dimension_id=dimension_id, period=period,
        )
        return WellbeingAggregateResponse(
            dimension_id=dimension_id, period=period, average_score=average, participant_count=count, suppressed=suppressed,
        )
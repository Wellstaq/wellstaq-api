"""
app/routers/wellbeing_surveys.py

Access model:
  Admin config (cadence) — org.settings.update, org-wide.
  Open window + submit — any active org member, self-only (their own
  responses).
  Live pulse / snapshot history — wellbeing.view_team (aggregate only,
  same as the wellbeing score aggregate routes — never individual data).
  My responses — self-only, personal history.

Routes:
  PUT  /organizations/{org_id}/wellbeing-survey/config
  GET  /organizations/{org_id}/wellbeing-survey/config
  GET  /organizations/{org_id}/wellbeing-survey/open-window
  POST /organizations/{org_id}/wellbeing-survey/responses
  GET  /organizations/{org_id}/wellbeing-survey/responses/me
  GET  /organizations/{org_id}/wellbeing-survey/pulse/live
  GET  /organizations/{org_id}/wellbeing-survey/pulse/history
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import WellbeingSurveyResponse
from app.auth.dependencies import(
     CurrentUser,
     get_current_user,
     require_permission,
     require_same_organization,
     require_scoped_permission,
     require_active_subscription,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.models.wellbeing_survey import PULSE_QUESTIONS
from app.schemas.wellbeing_survey import (
    LivePulseResponse,
    MyResponseListResponse,
    OpenWindowResponse,
    PulseQuestionResult,
    PulseSnapshotListResponse,
    PulseSnapshotResponse,
    SurveyCadenceRequest,
    SurveyConfigResponse,
    SurveyQuestionInfo,
    SurveySubmitRequest,
    MyResponseItem,
)
from app.services.wellbeing_survey import (
    get_live_pulse,
    get_my_responses,
    get_pulse_history,
    get_survey_config,
    set_survey_cadence,
    submit_survey_response,
)
from app.services.wellbeing_survey._helpers import get_open_window_or_404

router = APIRouter(prefix="/organizations/{org_id}/wellbeing-survey", tags=["wellbeing-survey"])


# ── Admin config ─────────────────────────────────────────────────────────────

@router.put("/config", response_model=SurveyConfigResponse, status_code=status.HTTP_200_OK)
async def set_cadence_route(
    org_id: str, body: SurveyCadenceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("org.settings.update")),
) -> SurveyConfigResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/wellbeing-survey/config"):
        config = await set_survey_cadence(session=db, organization_id=org_id, cadence=body.cadence)
        await db.commit()
        return SurveyConfigResponse.model_validate(config)


@router.get("/config", response_model=SurveyConfigResponse, status_code=status.HTTP_200_OK)
async def get_cadence_route(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> SurveyConfigResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/wellbeing-survey/config"):
        from fastapi import HTTPException
        config = await get_survey_config(session=db, organization_id=org_id)
        if config is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No survey cadence configured for this organization.")
        return SurveyConfigResponse.model_validate(config)


# ── Member: open window + submit ─────────────────────────────────────────────

@router.get("/open-window", response_model=OpenWindowResponse, status_code=status.HTTP_200_OK)
async def get_open_window_route(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> OpenWindowResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/wellbeing-survey/open-window"):
        window = await get_open_window_or_404(session=db, organization_id=org_id)

        existing = await db.execute(
            select(WellbeingSurveyResponse.id).where(
                WellbeingSurveyResponse.window_id == window.id, WellbeingSurveyResponse.user_id == current_user.user_id,
            ).limit(1)
        )
        already_responded = existing.scalar_one_or_none() is not None

        return OpenWindowResponse(
            window_id=window.id, window_start=window.window_start, window_end=window.window_end,
            already_responded=already_responded,
            questions=[SurveyQuestionInfo(question_key=k, question_text=v) for k, v in PULSE_QUESTIONS.items()],
        )


@router.post("/responses", status_code=status.HTTP_201_CREATED)
async def submit_response_route(
    org_id: str, body: SurveySubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/wellbeing-survey/responses"):
        await submit_survey_response(
            session=db, organization_id=org_id, user_id=current_user.user_id, answers=body.answers,
        )
        await db.commit()
        return {"message": "Survey response submitted."}


@router.get("/responses/me", response_model=MyResponseListResponse, status_code=status.HTTP_200_OK)
async def my_responses_route(
    org_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MyResponseListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/wellbeing-survey/responses/me"):
        responses = await get_my_responses(session=db, user_id=current_user.user_id, limit=limit)
        return MyResponseListResponse(items=[MyResponseItem.model_validate(r) for r in responses])


# ── Admin/manager: pulse aggregates ──────────────────────────────────────────
@router.get("/pulse/live", response_model=LivePulseResponse | list[LivePulseResponse], status_code=status.HTTP_200_OK)
async def live_pulse_route(
    org_id: str,
    branch_id: str | None = Query(default=None, description="Omit for org-wide, or scope to one branch."),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> LivePulseResponse | list[LivePulseResponse]:
    require_same_organization(org_id, current_user)

   
    await require_scoped_permission(
        db=db, current_user=current_user, permission="wellbeing.view_team", target_branch_id=branch_id,
    )

    async with request_guard(db, "/organizations/{org_id}/wellbeing-survey/pulse/live"):
        aggregation = await get_live_pulse(session=db, organization_id=org_id, branch_id=branch_id)
        if aggregation is None:
            return []

        pq = aggregation["per_question"]
        return LivePulseResponse(
            window_id=aggregation["window_id"], respondent_count=aggregation["respondent_count"],
            stress_manageability=PulseQuestionResult(**pq["stress_manageability"]),
            energy_recovery=PulseQuestionResult(**pq["energy_recovery"]),
            connection_belonging=PulseQuestionResult(**pq["connection_belonging"]),
            workload_sustainability=PulseQuestionResult(**pq["workload_sustainability"]),
            workplace_comfort=PulseQuestionResult(**pq["workplace_comfort"]),
            priority_support_pct=aggregation["priority_support_pct"],
            needs_attention_pct=aggregation["needs_attention_pct"],
            doing_well_pct=aggregation["doing_well_pct"],
        )


@router.get("/pulse/history", response_model=PulseSnapshotListResponse, status_code=status.HTTP_200_OK)
async def pulse_history_route(
    org_id: str,
    branch_id: str | None = Query(default=None),
    limit: int = Query(default=12, ge=1, le=52),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PulseSnapshotListResponse:
    require_same_organization(org_id, current_user)
    await require_scoped_permission(
        db=db, current_user=current_user, permission="wellbeing.view_team", target_branch_id=branch_id,
    )

    async with request_guard(db, "/organizations/{org_id}/wellbeing-survey/pulse/history"):
        snapshots = await get_pulse_history(session=db, organization_id=org_id, branch_id=branch_id, limit=limit)
        return PulseSnapshotListResponse(items=[PulseSnapshotResponse.model_validate(s) for s in snapshots])

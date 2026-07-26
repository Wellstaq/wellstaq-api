"""
app/services/wellbeing_survey/live_pulse.py

Computes the Pulse aggregation LIVE, on read, for the currently open
window — distinct from WellbeingPulseSnapshot, which is only written
once a window CLOSES. This lets the frontend show a live in-progress
percentage before the window's official snapshot exists (e.g. "so far
this month, 62% report manageable stress" while responses are still
coming in).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wellbeing_survey import PULSE_QUESTION_KEYS, WellbeingSurveyResponse, score_to_status
from app.services.wellbeing_survey._helpers import get_open_window_or_404


async def compute_pulse_aggregation(*, session: AsyncSession, window_id: str) -> dict:
    """Shared by both the live endpoint (open window) and the snapshot
    job (closing window) — same computation, different caller."""
    result = await session.execute(
        select(WellbeingSurveyResponse.user_id, WellbeingSurveyResponse.question_key, WellbeingSurveyResponse.response_value)
        .where(WellbeingSurveyResponse.window_id == window_id)
    )
    rows = result.all()

    if not rows:
        return {
            "per_question": {q: {"percent": None, "status": None} for q in PULSE_QUESTION_KEYS},
            "priority_support_pct": None, "needs_attention_pct": None, "doing_well_pct": None,
            "respondent_count": 0,
        }

    # Per-question: % answering 4-5, per question.
    by_question: dict[str, list[int]] = {q: [] for q in PULSE_QUESTION_KEYS}
    # Per-person: collect each person's full answer set to average.
    by_person: dict[str, dict[str, int]] = {}

    for user_id, question_key, value in rows:
        by_question[question_key].append(value)
        by_person.setdefault(user_id, {})[question_key] = value

    per_question = {}
    for question_key, values in by_question.items():
        if not values:
            per_question[question_key] = {"percent": None, "status": None}
            continue
        positive = sum(1 for v in values if v >= 4)
        percent = round((positive / len(values)) * 100, 2)
        per_question[question_key] = {"percent": percent, "status": score_to_status(percent)}

    # Per-person combined score -> band, only for people who answered ALL 5.
    bands = {"priority": 0, "attention": 0, "well": 0}
    complete_respondents = 0
    for user_id, answers in by_person.items():
        if set(answers.keys()) != set(PULSE_QUESTION_KEYS):
            continue  # incomplete submission shouldn't happen given submit validation, but guard anyway
        complete_respondents += 1
        avg = sum(answers.values()) / len(answers)
        combined_percent = (avg / 5) * 100
        status_label = score_to_status(combined_percent)
        if status_label == "Priority focus":
            bands["priority"] += 1
        elif status_label == "Needs attention":
            bands["attention"] += 1
        else:
            bands["well"] += 1

    if complete_respondents == 0:
        priority_pct = attention_pct = well_pct = None
    else:
        priority_pct = round((bands["priority"] / complete_respondents) * 100, 2)
        attention_pct = round((bands["attention"] / complete_respondents) * 100, 2)
        well_pct = round((bands["well"] / complete_respondents) * 100, 2)

    return {
        "per_question": per_question,
        "priority_support_pct": priority_pct, "needs_attention_pct": attention_pct, "doing_well_pct": well_pct,
        "respondent_count": complete_respondents,
    }


async def get_live_pulse(*, session: AsyncSession, organization_id: str) -> dict:
    window = await get_open_window_or_404(session=session, organization_id=organization_id)
    return await compute_pulse_aggregation(session=session, window_id=window.id)
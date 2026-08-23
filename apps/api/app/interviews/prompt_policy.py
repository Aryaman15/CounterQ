from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.examiner.models import ExaminerDecision
from app.interviews.models import InterviewerPrompt, InterviewerPromptDelivery


class PromptPolicyError(ValueError):
    pass


class PromptNotDeliverable(PromptPolicyError):
    pass


class DeliveryStateInvalid(PromptPolicyError):
    pass


def validate_prompt_origin(
    *,
    origin: str,
    examiner_decision: ExaminerDecision | None,
) -> None:
    if origin == "EXAMINER_DECISION":
        if examiner_decision is None:
            raise PromptNotDeliverable("Examiner-origin prompt requires ExaminerDecision")
        if examiner_decision.status not in {"AUTHORIZED", "PROPOSED"}:
            raise PromptNotDeliverable(
                "Rejected, stale or expired ExaminerDecision is not deliverable"
            )
        return
    if examiner_decision is not None:
        raise PromptNotDeliverable("Non-examiner prompt cannot carry ExaminerDecision provenance")


def validate_delivery_state(delivery: InterviewerPromptDelivery) -> None:
    if delivery.delivery_state == "STARTED":
        if delivery.completed_at is not None or delivery.interrupted_at is not None:
            raise DeliveryStateInvalid("STARTED delivery cannot be completed or interrupted")
    elif delivery.delivery_state in {"DELIVERED", "PARTIALLY_DELIVERED"}:
        if delivery.completed_at is None:
            raise DeliveryStateInvalid("Delivered attempt requires completed_at")
    elif delivery.delivery_state == "INTERRUPTED":
        if delivery.interrupted_at is None:
            raise DeliveryStateInvalid("Interrupted attempt requires interrupted_at")
    elif delivery.delivery_state == "CANCELLED":
        if delivery.actual_transcript_segment_id is not None:
            raise DeliveryStateInvalid("Cancelled attempt cannot have delivered transcript")
    else:
        raise DeliveryStateInvalid(f"Unknown delivery state: {delivery.delivery_state}")


def prompt_is_candidate_visible(prompt: InterviewerPrompt) -> bool:
    return prompt.status in {"DELIVERED", "ANSWERED", "INTERRUPTED"}


async def ensure_no_active_delivery(session: AsyncSession, interview_session_id: object) -> None:
    active_delivery_id = await session.scalar(
        select(InterviewerPromptDelivery.id)
        .where(InterviewerPromptDelivery.interview_session_id == interview_session_id)
        .where(InterviewerPromptDelivery.delivery_state == "STARTED")
        .limit(1),
    )
    if active_delivery_id is not None:
        raise PromptNotDeliverable("Another PromptDelivery already owns the conversation floor")


def now_utc() -> datetime:
    return datetime.now(UTC)

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

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


@dataclass(frozen=True)
class CandidateVisibleDelivery:
    prompt_id: UUID
    delivery_id: UUID
    actual_transcript_segment_id: UUID
    delivery_state: str
    is_partial: bool


def validate_prompt_origin(
    *,
    origin: str,
    examiner_decision: ExaminerDecision | None,
) -> None:
    if origin == "EXAMINER_DECISION":
        if examiner_decision is None:
            raise PromptNotDeliverable("Examiner-origin prompt requires ExaminerDecision")
        return
    if examiner_decision is not None:
        raise PromptNotDeliverable("Non-examiner prompt cannot carry ExaminerDecision provenance")


def validate_examiner_decision_delivery_eligibility(decision: ExaminerDecision) -> None:
    if decision.status != "AUTHORIZED" or decision.policy_gate_outcome != "AUTHORIZED":
        raise PromptNotDeliverable(
            "ExaminerDecision requires deterministic authorization before delivery"
        )


def validate_prompt_delivery_eligibility(
    *,
    prompt: InterviewerPrompt,
    examiner_decision: ExaminerDecision | None,
) -> None:
    if prompt.status != "AUTHORIZED":
        raise PromptNotDeliverable("Only authorized prompts are eligible for delivery")
    validate_prompt_origin(origin=prompt.origin, examiner_decision=examiner_decision)
    if prompt.origin == "EXAMINER_DECISION":
        if examiner_decision is None:
            raise PromptNotDeliverable("Examiner-origin prompt requires ExaminerDecision")
        validate_examiner_decision_delivery_eligibility(examiner_decision)


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


def candidate_visible_delivery(
    delivery: InterviewerPromptDelivery,
) -> CandidateVisibleDelivery | None:
    if delivery.delivery_state not in {"DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED"}:
        return None
    if delivery.actual_transcript_segment_id is None:
        return None
    return CandidateVisibleDelivery(
        prompt_id=delivery.interviewer_prompt_id,
        delivery_id=delivery.id,
        actual_transcript_segment_id=delivery.actual_transcript_segment_id,
        delivery_state=delivery.delivery_state,
        is_partial=delivery.delivery_state in {"PARTIALLY_DELIVERED", "INTERRUPTED"},
    )


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

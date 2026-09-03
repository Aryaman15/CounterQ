"""Candidate-safe Session Report status/read APIs and development inspection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.models import AIPolicyVersion
from app.config.environment import development_spike_enabled
from app.config.settings import Settings, get_settings
from app.db.session import get_session, get_sessionmaker
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.outbox.models import OutboxEvent
from app.outbox.repository import OutboxRepository
from app.problems.models import ProblemVersion
from app.reports.policy import SESSION_REPORT_POLICY_KEY, SESSION_REPORT_POLICY_VERSION
from app.reports.repository import SessionReportRepository
from app.reports.schema import SessionReportDocument
from app.reports.source import SessionReportSourceBuilder, SessionReportSourceUnavailable

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportSessionMetadata(BaseModel):
    problem_title: str
    mode: Literal["COACH", "SIMULATION"]
    language: str
    completed_at: datetime
    duration_seconds: int


class CandidateSessionReportResponse(BaseModel):
    status: Literal["NOT_STARTED", "PREPARING", "READY", "FAILED"]
    session: ReportSessionMetadata
    report_id: UUID | None
    report_version: int | None
    generated_at: datetime | None
    report: SessionReportDocument | None
    message: str


class DevelopmentRegenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)


class DevelopmentRegenerationResponse(BaseModel):
    outbox_event_id: UUID
    created: bool
    status: str


class DevelopmentReportInspection(BaseModel):
    interview_session_id: UUID
    outbox: list[dict[str, Any]]
    evidence_finalization_status: str
    report_status: str
    report_id: UUID | None
    report_version: int | None
    generation_policy: str | None
    ai_invocation_id: UUID | None
    source_evidence_count: int
    source_breakpoint_count: int
    report_validation_status: str | None
    is_current: bool
    last_failure_category: str | None


@router.get(
    "/sessions/{interview_session_id}",
    response_model=CandidateSessionReportResponse,
)
async def session_report_status(
    interview_session_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateSessionReportResponse:
    interview, configuration, problem = await _session_facts(session, interview_session_id)
    if interview.status != "COMPLETED" or interview.completed_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session Report is available only after interview completion",
        )
    metadata = ReportSessionMetadata(
        problem_title=problem.title,
        mode=configuration.mode,
        language=configuration.language,
        completed_at=interview.completed_at,
        duration_seconds=max(
            0, int((interview.completed_at - interview.started_at).total_seconds())
        ),
    )
    repository = SessionReportRepository(session)
    ready = await repository.current_ready(interview.id)
    if ready is not None and ready.structured_report_json is not None:
        return CandidateSessionReportResponse(
            status="READY",
            session=metadata,
            report_id=ready.id,
            report_version=ready.report_version,
            generated_at=ready.generated_at,
            report=SessionReportDocument.model_validate(ready.structured_report_json),
            message="Your evidence-backed Session Report is ready.",
        )
    latest = await repository.latest(interview.id)
    failed_event = await session.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.interview_session_id == interview.id,
            OutboxEvent.event_type.in_(("FINALIZE_SESSION_EVIDENCE", "GENERATE_SESSION_REPORT")),
            OutboxEvent.status == "FAILED",
        )
        .order_by(OutboxEvent.created_at.desc())
        .limit(1)
    )
    if failed_event is not None:
        return CandidateSessionReportResponse(
            status="FAILED",
            session=metadata,
            report_id=latest.id if latest else None,
            report_version=latest.report_version if latest else None,
            generated_at=None,
            report=None,
            message="Your interview is saved, but the detailed report could not be generated yet.",
        )
    pending_event = await session.scalar(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.interview_session_id == interview.id,
            OutboxEvent.event_type.in_(("FINALIZE_SESSION_EVIDENCE", "GENERATE_SESSION_REPORT")),
            OutboxEvent.status.in_(("PENDING", "PUBLISHED", "PROCESSING", "RETRY")),
        )
        .limit(1)
    )
    report_status: Literal["NOT_STARTED", "PREPARING"] = (
        "PREPARING" if pending_event is not None or latest is not None else "NOT_STARTED"
    )
    return CandidateSessionReportResponse(
        status=report_status,
        session=metadata,
        report_id=latest.id if latest else None,
        report_version=latest.report_version if latest else None,
        generated_at=None,
        report=None,
        message="CounterQ is reviewing what you demonstrated.",
    )


@router.post(
    "/development/sessions/{interview_session_id}/regenerate",
    response_model=DevelopmentRegenerationResponse,
)
async def regenerate_session_report(
    interview_session_id: UUID,
    request: DevelopmentRegenerationRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DevelopmentRegenerationResponse:
    _require_development(settings)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            interview = await session.scalar(
                select(InterviewSession)
                .where(InterviewSession.id == interview_session_id)
                .with_for_update()
            )
            if interview is None:
                raise HTTPException(status_code=404, detail="Interview session was not found")
            if interview.status != "COMPLETED":
                raise HTTPException(status_code=409, detail="Interview is not completed")
            generation_key = (
                f"session-report:{interview.id}:{SESSION_REPORT_POLICY_KEY}."
                f"{SESSION_REPORT_POLICY_VERSION}:regenerate:{request.idempotency_key}"
            )
            event, created = await OutboxRepository(session).enqueue(
                aggregate_type="InterviewSession",
                aggregate_id=interview.id,
                interview_session_id=interview.id,
                event_type="GENERATE_SESSION_REPORT",
                payload={
                    "interview_session_id": str(interview.id),
                    "generation_request_key": generation_key,
                    "report_policy": "session_report.v1",
                },
                deduplication_key=generation_key,
                available_at=datetime.now(UTC),
                source_watermark=interview.last_server_sequence,
            )
            return DevelopmentRegenerationResponse(
                outbox_event_id=event.id,
                created=created,
                status=event.status,
            )


@router.get(
    "/development/sessions/{interview_session_id}/inspection",
    response_model=DevelopmentReportInspection,
)
async def development_report_inspection(
    interview_session_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevelopmentReportInspection:
    _require_development(settings)
    interview = await session.get(InterviewSession, interview_session_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="Interview session was not found")
    outbox = list(
        await session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.interview_session_id == interview.id)
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
        )
    )
    latest = await SessionReportRepository(session).latest(interview.id)
    policy = (
        await session.get(AIPolicyVersion, latest.generation_policy_version_id)
        if latest is not None and latest.generation_policy_version_id is not None
        else None
    )
    try:
        source_bundle = await SessionReportSourceBuilder(session).build(interview.id)
    except SessionReportSourceUnavailable:
        evidence_count = breakpoint_count = 0
    else:
        evidence_count = len(source_bundle.evidence)
        breakpoint_count = len(source_bundle.breakpoints)
    finalization = next(
        (
            item.status
            for item in reversed(outbox)
            if item.event_type == "FINALIZE_SESSION_EVIDENCE"
        ),
        "NOT_STARTED",
    )
    return DevelopmentReportInspection(
        interview_session_id=interview.id,
        outbox=[
            {
                "id": item.id,
                "event_type": item.event_type,
                "status": item.status,
                "attempt_count": item.attempt_count,
                "last_error": item.last_error,
            }
            for item in outbox
        ],
        evidence_finalization_status=finalization,
        report_status=latest.status if latest else "NOT_STARTED",
        report_id=latest.id if latest else None,
        report_version=latest.report_version if latest else None,
        generation_policy=(f"{policy.policy_key}/{policy.version}" if policy else None),
        ai_invocation_id=latest.generation_ai_invocation_id if latest else None,
        source_evidence_count=evidence_count,
        source_breakpoint_count=breakpoint_count,
        report_validation_status=latest.validation_status if latest else None,
        is_current=latest.is_current if latest else False,
        last_failure_category=(
            latest.last_failure_category
            if latest and latest.last_failure_category
            else next((item.last_error for item in reversed(outbox) if item.last_error), None)
        ),
    )


async def _session_facts(
    session: AsyncSession, session_id: UUID
) -> tuple[InterviewSession, InterviewConfiguration, ProblemVersion]:
    interview = await session.get(InterviewSession, session_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="Interview session was not found")
    configuration = await session.get(InterviewConfiguration, interview.interview_configuration_id)
    problem = await session.get(ProblemVersion, interview.problem_version_id)
    if configuration is None or problem is None:
        raise HTTPException(status_code=500, detail="Session metadata is unavailable")
    return interview, configuration, problem


def _require_development(settings: Settings) -> None:
    if not development_spike_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "Session Report regeneration and inspection are development-only",
            },
        )

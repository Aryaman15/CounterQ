"""Evidence-first Session Report synthesis and deterministic admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import AIGateway, AIGatewayError
from app.ai_gateway.provider import ReasoningProviderError
from app.interviews.models import InterviewSession
from app.outbox.claims import OutboxWorkClaim
from app.outbox.models import OutboxEvent
from app.reports.models import SessionReport
from app.reports.policy import (
    SESSION_REPORT_INSTRUCTIONS,
    SESSION_REPORT_POLICY_KEY,
    SESSION_REPORT_POLICY_VERSION,
    SESSION_REPORT_PURPOSE,
    session_report_policy_descriptor,
)
from app.reports.repository import SessionReportRepository
from app.reports.schema import (
    SessionReportDocument,
    SessionReportSynthesis,
    build_candidate_document,
)
from app.reports.source import SessionReportSourceBuilder
from app.reports.validator import SessionReportValidationError, SessionReportValidator


class SessionReportGenerationError(RuntimeError):
    def __init__(self, category: str, safe_message: str) -> None:
        self.category = category
        self.safe_message = safe_message
        super().__init__(safe_message)


class SessionReportWorkOwnershipLost(SessionReportGenerationError):
    def __init__(self) -> None:
        super().__init__(
            "OUTBOX_OWNERSHIP_LOST",
            "Session Report work claim is no longer current",
        )


@dataclass(frozen=True)
class SessionReportGenerationResult:
    report_id: UUID
    report_version: int
    created: bool
    ai_invocation_id: UUID
    source_identity: str


class SessionReportGenerationService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        ai_gateway: AIGateway,
        validator: SessionReportValidator | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._gateway = ai_gateway
        self._validator = validator or SessionReportValidator()

    async def generate(
        self,
        *,
        interview_session_id: UUID,
        generation_request_key: str,
        work_claim: OutboxWorkClaim | None = None,
    ) -> SessionReportGenerationResult:
        if work_claim is not None:
            async with self._sessionmaker() as ownership_session:
                async with ownership_session.begin():
                    await _require_work_claim(
                        ownership_session,
                        work_claim=work_claim,
                        interview_session_id=interview_session_id,
                    )
        async with self._sessionmaker() as read_session:
            bundle = await SessionReportSourceBuilder(read_session).build(interview_session_id)

        source_changed = False
        async with self._sessionmaker() as session:
            async with session.begin():
                if work_claim is not None:
                    await _require_work_claim(
                        session,
                        work_claim=work_claim,
                        interview_session_id=interview_session_id,
                    )
                report, should_generate = await SessionReportRepository(session).prepare_generation(
                    session_id=interview_session_id,
                    generation_request_key=generation_request_key,
                    source_watermark=bundle.session.source_watermark,
                    source_identity=bundle.source_identity,
                )
                report_id = report.id
                report_version = report.report_version
                if not should_generate:
                    if report.generation_ai_invocation_id is None:
                        raise SessionReportGenerationError(
                            "REPORT_PROVENANCE_MISSING",
                            "Existing ready report is missing generation provenance",
                        )
                    return SessionReportGenerationResult(
                        report_id=report.id,
                        report_version=report.report_version,
                        created=False,
                        ai_invocation_id=report.generation_ai_invocation_id,
                        source_identity=report.source_identity,
                    )

        gateway_result = None
        try:
            for attempt in (1, 2):
                try:
                    gateway_result = await self._gateway.reason_structured(
                        interview_session_id=interview_session_id,
                        capability="STANDARD_REASONING",
                        purpose=SESSION_REPORT_PURPOSE,
                        policy=session_report_policy_descriptor(),
                        instructions=SESSION_REPORT_INSTRUCTIONS,
                        input_content=bundle.serialize_for_ai(),
                        output_model=SessionReportSynthesis,
                        correlation_id=f"{generation_request_key}:attempt:{attempt}",
                        metadata={
                            "report_id": str(report_id),
                            "report_version": report_version,
                            "source_identity": bundle.source_identity,
                            "attempt": attempt,
                        },
                    )
                except AIGatewayError as exc:
                    if attempt == 1 and exc.category == "STRUCTURED_OUTPUT_INVALID":
                        continue
                    raise
                else:
                    break
            if gateway_result is None:
                raise SessionReportGenerationError(
                    "REPORT_SYNTHESIS_FAILED", "Report synthesis did not return a result"
                )
        except SessionReportWorkOwnershipLost:
            raise
        except (AIGatewayError, ReasoningProviderError, SessionReportGenerationError) as exc:
            category = getattr(exc, "category", type(exc).__name__)
            async with self._sessionmaker() as session:
                async with session.begin():
                    if work_claim is not None:
                        await _require_work_claim(
                            session,
                            work_claim=work_claim,
                            interview_session_id=interview_session_id,
                        )
                    await SessionReportRepository(session).mark_failed(
                        report_id=report_id,
                        category=category,
                        validation_failed=False,
                    )
            raise SessionReportGenerationError(
                category,
                "Session Report synthesis could not be completed",
            ) from exc

        try:
            self._validator.validate(bundle=bundle, report=gateway_result.parsed)
        except SessionReportValidationError as exc:
            async with self._sessionmaker() as session:
                async with session.begin():
                    if work_claim is not None:
                        await _require_work_claim(
                            session,
                            work_claim=work_claim,
                            interview_session_id=interview_session_id,
                        )
                    await SessionReportRepository(session).mark_failed(
                        report_id=report_id,
                        category="REPORT_VALIDATION_FAILED",
                        validation_failed=True,
                        invocation_id=gateway_result.invocation_id,
                        policy_version_id=gateway_result.policy_version_id,
                    )
            raise SessionReportGenerationError(
                "REPORT_VALIDATION_FAILED",
                "Session Report did not pass source validation",
            ) from exc

        async with self._sessionmaker() as session:
            async with session.begin():
                if work_claim is not None:
                    await _require_work_claim(
                        session,
                        work_claim=work_claim,
                        interview_session_id=interview_session_id,
                    )
                locked_interview = await session.scalar(
                    select(InterviewSession)
                    .where(InterviewSession.id == interview_session_id)
                    .with_for_update()
                )
                if locked_interview is None:
                    raise SessionReportGenerationError(
                        "SESSION_NOT_FOUND", "Interview session was not found"
                    )
                fresh_bundle = await SessionReportSourceBuilder(session).build(interview_session_id)
                repository = SessionReportRepository(session)
                if fresh_bundle.source_identity != bundle.source_identity:
                    await repository.mark_stale(report_id)
                    source_changed = True
                else:
                    self._validator.validate(bundle=fresh_bundle, report=gateway_result.parsed)
                    pending_report = await session.get(SessionReport, report_id)
                    if pending_report is None:
                        raise SessionReportGenerationError(
                            "REPORT_NOT_FOUND", "Pending Session Report disappeared"
                        )
                    document = build_candidate_document(fresh_bundle, gateway_result.parsed)
                    await repository.mark_ready(
                        report=pending_report,
                        structured_report_json=document.model_dump(mode="json"),
                        generation_ai_invocation_id=gateway_result.invocation_id,
                        generation_policy_version_id=gateway_result.policy_version_id,
                        generated_at=datetime.now(UTC),
                    )
        if source_changed:
            raise SessionReportGenerationError(
                "SOURCE_CHANGED",
                "Canonical report sources changed during generation",
            )
        return SessionReportGenerationResult(
            report_id=report_id,
            report_version=report_version,
            created=True,
            ai_invocation_id=gateway_result.invocation_id,
            source_identity=bundle.source_identity,
        )


async def load_report_document(report: SessionReport) -> SessionReportDocument:
    if report.status != "READY" or report.structured_report_json is None:
        raise ValueError("Session Report is not candidate-ready")
    return SessionReportDocument.model_validate(report.structured_report_json)


def initial_report_generation_key(session_id: UUID) -> str:
    return (
        f"session-report:{session_id}:{SESSION_REPORT_POLICY_KEY}."
        f"{SESSION_REPORT_POLICY_VERSION}:initial"
    )


async def _require_work_claim(
    session: AsyncSession,
    *,
    work_claim: OutboxWorkClaim,
    interview_session_id: UUID,
) -> OutboxEvent:
    event = await session.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.id == work_claim.outbox_event_id)
        .with_for_update()
    )
    if (
        event is None
        or event.interview_session_id != interview_session_id
        or event.event_type != "GENERATE_SESSION_REPORT"
        or event.attempt_count != work_claim.attempt
        or event.status != "PROCESSING"
    ):
        raise SessionReportWorkOwnershipLost()
    return event

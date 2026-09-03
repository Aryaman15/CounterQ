"""Persistence primitives for the rebuildable Session Report projection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import InterviewSession
from app.reports.models import SessionReport


class SessionReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current_ready(self, session_id: UUID) -> SessionReport | None:
        value = await self._session.scalar(
            select(SessionReport).where(
                SessionReport.interview_session_id == session_id,
                SessionReport.status == "READY",
                SessionReport.is_current.is_(True),
            )
        )
        return cast(SessionReport | None, value)

    async def latest(self, session_id: UUID) -> SessionReport | None:
        value = await self._session.scalar(
            select(SessionReport)
            .where(SessionReport.interview_session_id == session_id)
            .order_by(SessionReport.report_version.desc())
            .limit(1)
        )
        return cast(SessionReport | None, value)

    async def latest_for_request(
        self, *, session_id: UUID, generation_request_key: str
    ) -> SessionReport | None:
        value = await self._session.scalar(
            select(SessionReport)
            .where(
                SessionReport.interview_session_id == session_id,
                SessionReport.generation_request_key == generation_request_key,
            )
            .order_by(SessionReport.report_version.desc())
            .limit(1)
        )
        return cast(SessionReport | None, value)

    async def prepare_generation(
        self,
        *,
        session_id: UUID,
        generation_request_key: str,
        source_watermark: int,
        source_identity: str,
    ) -> tuple[SessionReport, bool]:
        interview = await self._session.scalar(
            select(InterviewSession).where(InterviewSession.id == session_id).with_for_update()
        )
        if interview is None or interview.status != "COMPLETED":
            raise ValueError("Session Report requires a completed interview")
        existing = await self.latest_for_request(
            session_id=session_id,
            generation_request_key=generation_request_key,
        )
        if existing is not None and existing.status != "SUPERSEDED":
            if existing.status == "READY":
                return existing, False
            existing.status = "GENERATING"
            existing.validation_status = "PENDING"
            existing.source_watermark = source_watermark
            existing.source_identity = source_identity
            existing.last_failure_category = None
            existing.updated_at = datetime.now(UTC)
            await self._session.flush()
            return existing, True
        next_version = (
            int(
                await self._session.scalar(
                    select(func.coalesce(func.max(SessionReport.report_version), 0)).where(
                        SessionReport.interview_session_id == session_id
                    )
                )
                or 0
            )
            + 1
        )
        report = SessionReport(
            interview_session_id=session_id,
            report_version=next_version,
            generation_request_key=generation_request_key,
            status="GENERATING",
            validation_status="PENDING",
            structured_report_json=None,
            rendered_markdown=None,
            generation_ai_invocation_id=None,
            generation_policy_version_id=None,
            source_watermark=source_watermark,
            source_identity=source_identity,
            generated_at=None,
            is_current=False,
        )
        self._session.add(report)
        await self._session.flush()
        return report, True

    async def mark_ready(
        self,
        *,
        report: SessionReport,
        structured_report_json: dict[str, object],
        generation_ai_invocation_id: UUID,
        generation_policy_version_id: UUID,
        generated_at: datetime,
    ) -> None:
        previous = list(
            await self._session.scalars(
                select(SessionReport)
                .where(
                    SessionReport.interview_session_id == report.interview_session_id,
                    SessionReport.is_current.is_(True),
                    SessionReport.id != report.id,
                )
                .with_for_update()
            )
        )
        for old in previous:
            old.is_current = False
            old.status = "SUPERSEDED"
            old.updated_at = generated_at
        report.structured_report_json = structured_report_json
        report.generation_ai_invocation_id = generation_ai_invocation_id
        report.generation_policy_version_id = generation_policy_version_id
        report.status = "READY"
        report.validation_status = "PASSED"
        report.generated_at = generated_at
        report.is_current = True
        report.last_failure_category = None
        report.updated_at = generated_at
        await self._session.flush()

    async def mark_failed(
        self,
        *,
        report_id: UUID,
        category: str,
        validation_failed: bool,
        invocation_id: UUID | None = None,
        policy_version_id: UUID | None = None,
    ) -> None:
        report = await self._session.scalar(
            select(SessionReport).where(SessionReport.id == report_id).with_for_update()
        )
        if report is None or report.status == "READY":
            return
        report.status = "FAILED"
        report.validation_status = "FAILED" if validation_failed else "PENDING"
        report.last_failure_category = category[:128]
        report.generation_ai_invocation_id = invocation_id
        report.generation_policy_version_id = policy_version_id
        report.structured_report_json = None
        report.is_current = False
        report.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def mark_stale(self, report_id: UUID) -> None:
        report = await self._session.scalar(
            select(SessionReport).where(SessionReport.id == report_id).with_for_update()
        )
        if report is None or report.status == "READY":
            return
        report.status = "SUPERSEDED"
        report.validation_status = "FAILED"
        report.last_failure_category = "SOURCE_CHANGED"
        report.is_current = False
        report.updated_at = datetime.now(UTC)
        await self._session.flush()

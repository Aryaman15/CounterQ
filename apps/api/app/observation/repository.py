from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observation.models import CodeDiff, CodeSnapshot, InterviewEvent, TranscriptSegment


class ObservationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_transcript_segment(
        self,
        *,
        session_id: UUID,
        event_id: UUID,
        speaker: str,
        sequence: int,
        started_at: datetime,
        text: str,
        interview_stage: str,
        interview_state_version: int,
        ended_at: datetime | None = None,
        provider_confidence: Decimal | None = None,
        delivery_state: str | None = None,
        interrupted_at: datetime | None = None,
        provider_segment_id: str | None = None,
    ) -> TranscriptSegment:
        segment = TranscriptSegment(
            interview_session_id=session_id,
            interview_event_id=event_id,
            speaker=speaker,
            sequence=sequence,
            started_at=started_at,
            ended_at=ended_at,
            text=text,
            provider_confidence=provider_confidence,
            interview_stage=interview_stage,
            interview_state_version=interview_state_version,
            delivery_state=delivery_state,
            interrupted_at=interrupted_at,
            provider_segment_id=provider_segment_id,
        )
        self._session.add(segment)
        await self._session.flush()
        return segment

    async def add_code_snapshot(
        self,
        *,
        session_id: UUID,
        version_number: int,
        language: str,
        source_code: str,
        content_hash: str,
        created_from_event_id: UUID,
        parent_snapshot_id: UUID | None = None,
    ) -> CodeSnapshot:
        snapshot = CodeSnapshot(
            interview_session_id=session_id,
            version_number=version_number,
            parent_snapshot_id=parent_snapshot_id,
            language=language,
            source_code=source_code,
            content_hash=content_hash,
            created_from_event_id=created_from_event_id,
        )
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot

    async def add_code_diff(
        self,
        *,
        session_id: UUID,
        from_snapshot_id: UUID,
        to_snapshot_id: UUID,
        diff_format: str,
        diff_content: str,
        created_from_event_id: UUID,
        change_summary: str | None = None,
        significance: str | None = None,
    ) -> CodeDiff:
        diff = CodeDiff(
            interview_session_id=session_id,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            diff_format=diff_format,
            diff_content=diff_content,
            change_summary=change_summary,
            significance=significance,
            created_from_event_id=created_from_event_id,
        )
        self._session.add(diff)
        await self._session.flush()
        return diff

    async def latest_code_snapshot(self, session_id: UUID) -> CodeSnapshot | None:
        snapshot = await self._session.scalar(
            select(CodeSnapshot)
            .where(CodeSnapshot.interview_session_id == session_id)
            .order_by(CodeSnapshot.version_number.desc())
            .limit(1),
        )
        return cast(CodeSnapshot | None, snapshot)

    async def code_snapshot_for_event(self, event_id: UUID) -> CodeSnapshot | None:
        snapshot = await self._session.scalar(
            select(CodeSnapshot).where(CodeSnapshot.created_from_event_id == event_id),
        )
        return cast(CodeSnapshot | None, snapshot)

    async def code_diff_for_event(self, event_id: UUID) -> CodeDiff | None:
        diff = await self._session.scalar(
            select(CodeDiff).where(CodeDiff.created_from_event_id == event_id),
        )
        return cast(CodeDiff | None, diff)

    async def latest_code_snapshot_at_or_before_event(
        self,
        *,
        session_id: UUID,
        server_sequence: int,
    ) -> CodeSnapshot | None:
        snapshot = await self._session.scalar(
            select(CodeSnapshot)
            .join(InterviewEvent, CodeSnapshot.created_from_event_id == InterviewEvent.id)
            .where(CodeSnapshot.interview_session_id == session_id)
            .where(InterviewEvent.interview_session_id == session_id)
            .where(InterviewEvent.server_sequence <= server_sequence)
            .order_by(CodeSnapshot.version_number.desc())
            .limit(1),
        )
        return cast(CodeSnapshot | None, snapshot)

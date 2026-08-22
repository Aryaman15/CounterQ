from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.observation.models import CodeSnapshot, TranscriptSegment


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

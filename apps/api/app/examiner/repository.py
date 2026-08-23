from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.examiner.models import CandidateClaim, ExaminerDecision


class ExaminerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_candidate_claim(
        self,
        *,
        interview_session_id: UUID,
        origin_kind: str,
        normalized_claim: str,
        claim_type: str,
        extraction_confidence: Decimal,
        status: str,
        ai_invocation_id: UUID,
        ai_policy_version_id: UUID,
        source_transcript_segment_id: UUID | None = None,
        source_event_id: UUID | None = None,
        source_code_snapshot_id: UUID | None = None,
        source_code_diff_id: UUID | None = None,
        verbatim_excerpt: str | None = None,
    ) -> CandidateClaim:
        claim = CandidateClaim(
            interview_session_id=interview_session_id,
            origin_kind=origin_kind,
            source_transcript_segment_id=source_transcript_segment_id,
            source_event_id=source_event_id,
            source_code_snapshot_id=source_code_snapshot_id,
            source_code_diff_id=source_code_diff_id,
            verbatim_excerpt=verbatim_excerpt,
            normalized_claim=normalized_claim,
            claim_type=claim_type,
            extraction_confidence=extraction_confidence,
            status=status,
            ai_invocation_id=ai_invocation_id,
            ai_policy_version_id=ai_policy_version_id,
        )
        self._session.add(claim)
        await self._session.flush()
        return claim

    async def add_examiner_decision(
        self,
        *,
        interview_session_id: UUID,
        action: str,
        technical_rationale: str,
        source_event_watermark: int,
        source_state_version: int,
        status: str,
        ai_invocation_id: UUID,
        ai_policy_version_id: UUID,
        target_claim_id: UUID | None = None,
        target_event_id: UUID | None = None,
        target_code_snapshot_id: UUID | None = None,
        proposed_probe_strategy: str | None = None,
        confidence: Decimal | None = None,
        priority: int | None = None,
        urgency: int | None = None,
        deadline_at: datetime | None = None,
        expiry_policy: str | None = None,
        policy_gate_outcome: str | None = None,
        policy_gate_reason: str | None = None,
    ) -> ExaminerDecision:
        decision = ExaminerDecision(
            interview_session_id=interview_session_id,
            action=action,
            target_claim_id=target_claim_id,
            target_event_id=target_event_id,
            target_code_snapshot_id=target_code_snapshot_id,
            proposed_probe_strategy=proposed_probe_strategy,
            technical_rationale=technical_rationale,
            confidence=confidence,
            priority=priority,
            urgency=urgency,
            source_event_watermark=source_event_watermark,
            source_state_version=source_state_version,
            deadline_at=deadline_at,
            expiry_policy=expiry_policy,
            policy_gate_outcome=policy_gate_outcome,
            policy_gate_reason=policy_gate_reason,
            status=status,
            ai_invocation_id=ai_invocation_id,
            ai_policy_version_id=ai_policy_version_id,
        )
        self._session.add(decision)
        await self._session.flush()
        return decision

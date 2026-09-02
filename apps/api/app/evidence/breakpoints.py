from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.constants import ASSESSMENT_DIMENSIONS, BREAKPOINT_STATUSES
from app.db.ids import uuid7
from app.evidence.models import (
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    SkillDimension,
)
from app.interviews.models import InterviewSession
from app.problems.models import Concept

MIN_BREAKPOINT_EVIDENCE_CONFIDENCE = Decimal("0.7000")
QUALIFYING_EVIDENCE_STRENGTHS = frozenset(("MODERATE", "STRONG"))
ACTIVE_BREAKPOINT_STATUSES = ("OPEN", "RETEST_PENDING", "IMPROVING")

MEANINGFUL_TECHNICAL_BOUNDARY = "MEANINGFUL_TECHNICAL_BOUNDARY"
NON_QUALIFYING_BOUNDARY_KINDS = frozenset(
    ("SYNTAX_ERROR", "TRANSIENT_SLIP", "TRANSCRIPTION_AMBIGUITY", "COSMETIC_ISSUE")
)

KNOWN_BREAKPOINT_SUBTYPES = frozenset(
    ("worst_case_complexity", "left_pointer_monotonicity", "recursive_stack_space")
)
KNOWN_BREAKPOINT_KEYS = {
    ("hash_table_complexity", "worst_case_complexity"): "hash_table_worst_case_complexity",
    (
        "sliding_window_invariant",
        "left_pointer_monotonicity",
    ): "sliding_window_left_pointer_monotonicity",
    ("space_complexity", "recursive_stack_space"): "recursive_stack_space",
}


@dataclass(frozen=True)
class BreakpointCandidate:
    user_id: UUID
    interview_session_id: UUID
    concept_id: UUID
    skill_dimension_id: UUID
    assessment_dimension: str
    evidence_ids: tuple[UUID, ...]
    boundary_kind: str
    summary: str
    severity: str
    known_subtype: str | None = None


@dataclass(frozen=True)
class BreakpointEligibility:
    eligible: bool
    reason: str


@dataclass(frozen=True)
class BreakpointPolicyResult:
    eligibility: BreakpointEligibility
    breakpoint_id: UUID | None = None
    breakpoint_key: str | None = None
    created: bool = False


class BreakpointPolicyError(ValueError):
    pass


class BreakpointService:
    """Deterministic Breakpoint qualification and idempotent persistence boundary."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def evaluate(self, candidate: BreakpointCandidate) -> BreakpointEligibility:
        if candidate.boundary_kind in NON_QUALIFYING_BOUNDARY_KINDS:
            return BreakpointEligibility(False, "TRIVIAL_OR_TRANSIENT_BOUNDARY")
        if candidate.boundary_kind != MEANINGFUL_TECHNICAL_BOUNDARY:
            return BreakpointEligibility(False, "UNSUPPORTED_BOUNDARY_KIND")
        if not candidate.evidence_ids:
            return BreakpointEligibility(False, "VALID_EVIDENCE_REQUIRED")
        if len(candidate.evidence_ids) != len(set(candidate.evidence_ids)):
            return BreakpointEligibility(False, "DUPLICATE_EVIDENCE")
        if candidate.assessment_dimension not in ASSESSMENT_DIMENSIONS:
            return BreakpointEligibility(False, "ASSESSMENT_DIMENSION_UNSUPPORTED")

        interview = await self._session.get(InterviewSession, candidate.interview_session_id)
        if interview is None or interview.user_id != candidate.user_id:
            return BreakpointEligibility(False, "SESSION_OWNERSHIP_MISMATCH")
        concept = await self._session.get(Concept, candidate.concept_id)
        skill = await self._session.get(SkillDimension, candidate.skill_dimension_id)
        if (
            concept is None
            or concept.status != "ACTIVE"
            or skill is None
            or skill.status != "ACTIVE"
        ):
            return BreakpointEligibility(False, "CANONICAL_TARGET_REQUIRED")

        evidence_rows = list(
            await self._session.scalars(
                select(Evidence).where(Evidence.id.in_(candidate.evidence_ids))
            )
        )
        if len(evidence_rows) != len(candidate.evidence_ids):
            return BreakpointEligibility(False, "VALID_EVIDENCE_REQUIRED")

        session_ids = {evidence.interview_session_id for evidence in evidence_rows}
        sessions = list(
            await self._session.scalars(
                select(InterviewSession).where(InterviewSession.id.in_(session_ids))
            )
        )
        if len(sessions) != len(session_ids) or any(
            session.user_id != candidate.user_id for session in sessions
        ):
            return BreakpointEligibility(False, "EVIDENCE_OWNERSHIP_MISMATCH")
        if any(
            evidence.validation_status != "VALID" or evidence.invalidated_at is not None
            for evidence in evidence_rows
        ):
            return BreakpointEligibility(False, "ACTIVE_VALID_EVIDENCE_REQUIRED")
        if any(evidence.polarity not in ("NEGATIVE", "MIXED") for evidence in evidence_rows):
            return BreakpointEligibility(False, "NEGATIVE_OR_MIXED_EVIDENCE_REQUIRED")
        if any(
            evidence.evidence_type != candidate.assessment_dimension for evidence in evidence_rows
        ):
            return BreakpointEligibility(False, "EVIDENCE_DIMENSION_MISMATCH")

        qualifying = [
            evidence
            for evidence in evidence_rows
            if evidence.strength in QUALIFYING_EVIDENCE_STRENGTHS
            and evidence.confidence >= MIN_BREAKPOINT_EVIDENCE_CONFIDENCE
        ]
        if not qualifying:
            return BreakpointEligibility(False, "INSUFFICIENT_EVIDENCE_STRENGTH_OR_CONFIDENCE")

        evidence_ids = [evidence.id for evidence in evidence_rows]
        concept_links = set(
            await self._session.scalars(
                select(EvidenceConcept.evidence_id).where(
                    EvidenceConcept.evidence_id.in_(evidence_ids),
                    EvidenceConcept.concept_id == candidate.concept_id,
                )
            )
        )
        skill_links = set(
            await self._session.scalars(
                select(EvidenceSkill.evidence_id).where(
                    EvidenceSkill.evidence_id.in_(evidence_ids),
                    EvidenceSkill.skill_dimension_id == candidate.skill_dimension_id,
                )
            )
        )
        qualifying_ids = {evidence.id for evidence in qualifying}
        if not qualifying_ids.intersection(concept_links).intersection(skill_links):
            return BreakpointEligibility(False, "EVIDENCE_TARGET_MISMATCH")
        return BreakpointEligibility(True, "ELIGIBLE")

    async def create_or_reinforce(self, candidate: BreakpointCandidate) -> BreakpointPolicyResult:
        eligibility = await self.evaluate(candidate)
        if not eligibility.eligible:
            return BreakpointPolicyResult(eligibility=eligibility)
        if not candidate.summary.strip() or not candidate.severity.strip():
            raise BreakpointPolicyError("Breakpoint summary and severity are required")

        concept = await self._session.get(Concept, candidate.concept_id)
        skill = await self._session.get(SkillDimension, candidate.skill_dimension_id)
        assert concept is not None
        assert skill is not None
        breakpoint_key = normalize_breakpoint_key(
            concept_key=concept.canonical_key,
            skill_key=skill.canonical_key,
            assessment_dimension=candidate.assessment_dimension,
            known_subtype=candidate.known_subtype,
        )
        now = datetime.now(UTC)
        proposed_id = uuid7()
        inserted_id = await self._session.scalar(
            insert(Breakpoint)
            .values(
                id=proposed_id,
                user_id=candidate.user_id,
                concept_id=candidate.concept_id,
                skill_dimension_id=candidate.skill_dimension_id,
                breakpoint_key=breakpoint_key,
                first_detected_session_id=candidate.interview_session_id,
                first_detected_at=now,
                severity=candidate.severity.strip(),
                status="OPEN",
                summary=candidate.summary.strip(),
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    Breakpoint.user_id,
                    Breakpoint.concept_id,
                    Breakpoint.skill_dimension_id,
                    Breakpoint.breakpoint_key,
                ),
                index_where=text("status IN ('OPEN', 'RETEST_PENDING', 'IMPROVING')"),
            )
            .returning(Breakpoint.id)
        )
        created = inserted_id is not None
        breakpoint_id = inserted_id
        if breakpoint_id is None:
            breakpoint_id = await self._session.scalar(
                select(Breakpoint.id).where(
                    Breakpoint.user_id == candidate.user_id,
                    Breakpoint.concept_id == candidate.concept_id,
                    Breakpoint.skill_dimension_id == candidate.skill_dimension_id,
                    Breakpoint.breakpoint_key == breakpoint_key,
                    Breakpoint.status.in_(ACTIVE_BREAKPOINT_STATUSES),
                )
            )
        if breakpoint_id is None:
            raise BreakpointPolicyError("Active Breakpoint conflict could not be resolved")

        evidence_rows = list(
            await self._session.scalars(
                select(Evidence)
                .where(Evidence.id.in_(candidate.evidence_ids))
                .order_by(Evidence.created_at, Evidence.id)
            )
        )
        for index, evidence in enumerate(evidence_rows):
            relationship = "CREATED" if created and index == 0 else "REINFORCED"
            await self._session.execute(
                insert(BreakpointEvidence)
                .values(
                    breakpoint_id=breakpoint_id,
                    evidence_id=evidence.id,
                    relationship=relationship,
                )
                .on_conflict_do_nothing(
                    index_elements=(
                        BreakpointEvidence.breakpoint_id,
                        BreakpointEvidence.evidence_id,
                    )
                )
            )
        await self._session.flush()
        return BreakpointPolicyResult(
            eligibility=eligibility,
            breakpoint_id=breakpoint_id,
            breakpoint_key=breakpoint_key,
            created=created,
        )

    async def link_evidence(
        self, *, breakpoint_id: UUID, evidence_id: UUID, relationship: str
    ) -> None:
        if relationship not in ("CONTRADICTED", "RESOLUTION_SUPPORT"):
            raise BreakpointPolicyError("This API only accepts rebuttal/resolution Evidence links")
        breakpoint = await self._session.get(Breakpoint, breakpoint_id)
        evidence = await self._session.get(Evidence, evidence_id)
        if breakpoint is None or evidence is None:
            raise BreakpointPolicyError("Breakpoint and canonical Evidence must exist")
        session = await self._session.get(InterviewSession, evidence.interview_session_id)
        if (
            session is None
            or session.user_id != breakpoint.user_id
            or evidence.validation_status != "VALID"
            or evidence.invalidated_at is not None
        ):
            raise BreakpointPolicyError("Evidence is not active canonical support for this user")
        await self._session.execute(
            insert(BreakpointEvidence)
            .values(
                breakpoint_id=breakpoint_id,
                evidence_id=evidence_id,
                relationship=relationship,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    BreakpointEvidence.breakpoint_id,
                    BreakpointEvidence.evidence_id,
                )
            )
        )
        await self._session.flush()


def normalize_breakpoint_key(
    *,
    concept_key: str,
    skill_key: str,
    assessment_dimension: str,
    known_subtype: str | None,
) -> str:
    concept = _canonical_token(concept_key)
    skill = _canonical_token(skill_key)
    if assessment_dimension not in ASSESSMENT_DIMENSIONS:
        raise BreakpointPolicyError("Breakpoint assessment dimension is not controlled")
    dimension = _canonical_token(assessment_dimension.lower())
    if known_subtype is not None:
        subtype = _canonical_token(known_subtype)
        if subtype not in KNOWN_BREAKPOINT_SUBTYPES:
            raise BreakpointPolicyError("Breakpoint subtype is not controlled")
        return KNOWN_BREAKPOINT_KEYS.get((concept, subtype), f"{concept}_{subtype}")
    return f"{concept}_{skill}_{dimension}"


def _canonical_token(value: str) -> str:
    if not value or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in value
    ):
        raise BreakpointPolicyError("Breakpoint identity inputs must be canonical snake_case keys")
    if value.startswith("_") or value.endswith("_") or "__" in value:
        raise BreakpointPolicyError("Breakpoint identity inputs must be normalized")
    return value


assert set(ACTIVE_BREAKPOINT_STATUSES).issubset(BREAKPOINT_STATUSES)

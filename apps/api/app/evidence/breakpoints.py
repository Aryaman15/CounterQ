from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
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
    EvidenceSource,
    SkillDimension,
)
from app.evidence.source_admission import evidence_source_admission
from app.interviews.models import InterviewSession
from app.observation.models import InterviewEvent
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
        target_matching_ids = concept_links.intersection(skill_links)
        if not set(evidence_ids).issubset(target_matching_ids):
            return BreakpointEligibility(False, "EVIDENCE_TARGET_MISMATCH")
        qualifying_ids = {evidence.id for evidence in qualifying}
        if not qualifying_ids.intersection(target_matching_ids):
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
        evidence_rows = list(
            await self._session.scalars(
                select(Evidence)
                .where(Evidence.id.in_(candidate.evidence_ids))
                .order_by(Evidence.created_at, Evidence.id)
            )
        )
        earliest_qualifying_evidence = min(
            (
                evidence
                for evidence in evidence_rows
                if evidence.strength in QUALIFYING_EVIDENCE_STRENGTHS
                and evidence.confidence >= MIN_BREAKPOINT_EVIDENCE_CONFIDENCE
            ),
            key=lambda evidence: (evidence.created_at, evidence.id),
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
                first_detected_session_id=earliest_qualifying_evidence.interview_session_id,
                first_detected_at=earliest_qualifying_evidence.created_at,
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

        for evidence in evidence_rows:
            relationship = (
                "CREATED"
                if created and evidence.id == earliest_qualifying_evidence.id
                else "REINFORCED"
            )
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
        if not await self._evidence_targets_boundary(
            evidence_id=evidence.id,
            concept_id=breakpoint.concept_id,
            skill_dimension_id=breakpoint.skill_dimension_id,
        ):
            raise BreakpointPolicyError(
                "Evidence does not target the Breakpoint Concept and SkillDimension"
            )
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

    async def link_evidence_to_active_boundary(
        self,
        *,
        user_id: UUID,
        concept_id: UUID,
        skill_dimension_id: UUID,
        assessment_dimension: str,
        known_subtype: str | None,
        evidence_id: UUID,
        relationship: str,
    ) -> UUID | None:
        """Link rebuttal Evidence only to the exact normalized active boundary."""

        concept = await self._session.get(Concept, concept_id)
        skill = await self._session.get(SkillDimension, skill_dimension_id)
        if (
            concept is None
            or concept.status != "ACTIVE"
            or skill is None
            or skill.status != "ACTIVE"
        ):
            raise BreakpointPolicyError("Canonical Breakpoint targets must exist and be active")
        breakpoint_key = normalize_breakpoint_key(
            concept_key=concept.canonical_key,
            skill_key=skill.canonical_key,
            assessment_dimension=assessment_dimension,
            known_subtype=known_subtype,
        )
        breakpoint_id = await self._session.scalar(
            select(Breakpoint.id).where(
                Breakpoint.user_id == user_id,
                Breakpoint.concept_id == concept_id,
                Breakpoint.skill_dimension_id == skill_dimension_id,
                Breakpoint.breakpoint_key == breakpoint_key,
                Breakpoint.status.in_(ACTIVE_BREAKPOINT_STATUSES),
            )
        )
        if breakpoint_id is None:
            return None
        await self.link_evidence(
            breakpoint_id=breakpoint_id,
            evidence_id=evidence_id,
            relationship=relationship,
        )
        return breakpoint_id

    async def reinforce_from_independent_retest(
        self,
        *,
        breakpoint_id: UUID,
        user_id: UUID,
        concept_id: UUID,
        evidence_ids: tuple[UUID, ...],
    ) -> tuple[UUID, ...]:
        """Reinforce one exact active Breakpoint through its canonical evidence floor."""

        breakpoint = await self._exact_active_breakpoint(
            breakpoint_id=breakpoint_id,
            user_id=user_id,
            concept_id=concept_id,
        )
        if breakpoint is None:
            return ()
        linked: list[UUID] = []
        for evidence_id in evidence_ids:
            evidence = await self._qualifying_retest_evidence(
                breakpoint=breakpoint,
                evidence_id=evidence_id,
                allowed_polarities=frozenset(("NEGATIVE", "MIXED")),
            )
            if evidence is None or evidence.independence_level != "INDEPENDENT":
                continue
            if await self._persist_relationship(
                breakpoint_id=breakpoint.id,
                evidence_id=evidence.id,
                relationship="REINFORCED",
            ):
                linked.append(evidence.id)
        await self._session.flush()
        return tuple(linked)

    async def resolve_from_independent_retest(
        self,
        *,
        breakpoint_id: UUID,
        user_id: UUID,
        concept_id: UUID,
        evidence_ids: tuple[UUID, ...],
        structured_self_correction_ids: frozenset[UUID] = frozenset(),
        resolved_at: datetime,
    ) -> tuple[UUID, ...]:
        """Resolve one exact active Breakpoint only from strict independent retest proof."""

        breakpoint = await self._exact_active_breakpoint(
            breakpoint_id=breakpoint_id,
            user_id=user_id,
            concept_id=concept_id,
        )
        if breakpoint is None:
            return ()
        linked: list[UUID] = []
        for evidence_id in evidence_ids:
            evidence = await self._qualifying_retest_evidence(
                breakpoint=breakpoint,
                evidence_id=evidence_id,
                allowed_polarities=frozenset(("POSITIVE", "MIXED")),
            )
            if evidence is None or evidence.independence_level != "INDEPENDENT":
                continue
            if (
                evidence.polarity != "POSITIVE"
                and evidence.id not in structured_self_correction_ids
            ):
                continue
            if await self._persist_relationship(
                breakpoint_id=breakpoint.id,
                evidence_id=evidence.id,
                relationship="RESOLUTION_SUPPORT",
            ):
                linked.append(evidence.id)
        if not linked:
            return ()
        breakpoint.status = "RESOLVED"
        breakpoint.resolved_at = resolved_at
        breakpoint.resolution_reason = "INDEPENDENT_RETEST_VERIFIED"
        await self._session.flush()
        return tuple(linked)

    async def active_support_count(self, breakpoint_id: UUID) -> int:
        """Count current qualifying support without erasing historical links."""

        value = await self._session.scalar(
            select(func.count())
            .select_from(BreakpointEvidence)
            .join(Evidence, Evidence.id == BreakpointEvidence.evidence_id)
            .join(InterviewSession, InterviewSession.id == Evidence.interview_session_id)
            .join(EvidenceConcept, EvidenceConcept.evidence_id == Evidence.id)
            .join(EvidenceSkill, EvidenceSkill.evidence_id == Evidence.id)
            .join(Breakpoint, Breakpoint.id == BreakpointEvidence.breakpoint_id)
            .where(
                BreakpointEvidence.breakpoint_id == breakpoint_id,
                BreakpointEvidence.relationship.in_(("CREATED", "REINFORCED")),
                Evidence.validation_status == "VALID",
                Evidence.invalidated_at.is_(None),
                Evidence.polarity.in_(("NEGATIVE", "MIXED")),
                Evidence.strength.in_(QUALIFYING_EVIDENCE_STRENGTHS),
                Evidence.confidence >= MIN_BREAKPOINT_EVIDENCE_CONFIDENCE,
                InterviewSession.user_id == Breakpoint.user_id,
                EvidenceConcept.concept_id == Breakpoint.concept_id,
                EvidenceSkill.skill_dimension_id == Breakpoint.skill_dimension_id,
            )
        )
        return int(value or 0)

    async def active_resolution_support_count(self, breakpoint_id: UUID) -> int:
        """Count current strict resolution support while retaining historical links."""

        value = await self._session.scalar(
            select(func.count())
            .select_from(BreakpointEvidence)
            .join(Evidence, Evidence.id == BreakpointEvidence.evidence_id)
            .join(InterviewSession, InterviewSession.id == Evidence.interview_session_id)
            .join(EvidenceConcept, EvidenceConcept.evidence_id == Evidence.id)
            .join(EvidenceSkill, EvidenceSkill.evidence_id == Evidence.id)
            .join(Breakpoint, Breakpoint.id == BreakpointEvidence.breakpoint_id)
            .where(
                BreakpointEvidence.breakpoint_id == breakpoint_id,
                BreakpointEvidence.relationship == "RESOLUTION_SUPPORT",
                Evidence.validation_status == "VALID",
                Evidence.invalidated_at.is_(None),
                Evidence.polarity.in_(("POSITIVE", "MIXED")),
                Evidence.strength.in_(QUALIFYING_EVIDENCE_STRENGTHS),
                Evidence.confidence >= MIN_BREAKPOINT_EVIDENCE_CONFIDENCE,
                Evidence.independence_level == "INDEPENDENT",
                InterviewSession.user_id == Breakpoint.user_id,
                EvidenceConcept.concept_id == Breakpoint.concept_id,
                EvidenceSkill.skill_dimension_id == Breakpoint.skill_dimension_id,
            )
        )
        return int(value or 0)

    async def recalculate_support_for_evidence(
        self, evidence_id: UUID, *, recalculated_at: datetime | None = None
    ) -> tuple[UUID, ...]:
        """Recalculate diagnoses affected by invalidated support or resolution proof."""

        breakpoint_ids = tuple(
            await self._session.scalars(
                select(BreakpointEvidence.breakpoint_id).where(
                    BreakpointEvidence.evidence_id == evidence_id,
                    BreakpointEvidence.relationship.in_(
                        ("CREATED", "REINFORCED", "RESOLUTION_SUPPORT")
                    ),
                )
            )
        )
        recalculated: list[UUID] = []
        for breakpoint_id in breakpoint_ids:
            breakpoint = await self._session.scalar(
                select(Breakpoint).where(Breakpoint.id == breakpoint_id).with_for_update()
            )
            if breakpoint is None or breakpoint.status == "DISMISSED":
                continue
            negative_support = await self.active_support_count(breakpoint.id)
            if breakpoint.status == "RESOLVED":
                resolution_support = await self.active_resolution_support_count(breakpoint.id)
                if negative_support == 0:
                    breakpoint.status = "DISMISSED"
                    breakpoint.resolved_at = recalculated_at or datetime.now(UTC)
                    breakpoint.resolution_reason = "SUPPORT_INVALIDATED"
                elif resolution_support == 0:
                    breakpoint.status = "RETEST_PENDING"
                    breakpoint.resolved_at = None
                    breakpoint.resolution_reason = None
                else:
                    continue
                recalculated.append(breakpoint.id)
                continue
            if breakpoint.status not in ACTIVE_BREAKPOINT_STATUSES or negative_support > 0:
                continue
            breakpoint.status = "DISMISSED"
            breakpoint.resolved_at = recalculated_at or datetime.now(UTC)
            breakpoint.resolution_reason = "SUPPORT_INVALIDATED"
            recalculated.append(breakpoint.id)
        await self._session.flush()
        return tuple(recalculated)

    async def _exact_active_breakpoint(
        self,
        *,
        breakpoint_id: UUID,
        user_id: UUID,
        concept_id: UUID,
    ) -> Breakpoint | None:
        breakpoint: Breakpoint | None = await self._session.scalar(
            select(Breakpoint)
            .where(
                Breakpoint.id == breakpoint_id,
                Breakpoint.user_id == user_id,
                Breakpoint.concept_id == concept_id,
                Breakpoint.status.in_(ACTIVE_BREAKPOINT_STATUSES),
            )
            .with_for_update()
        )
        return breakpoint

    async def _qualifying_retest_evidence(
        self,
        *,
        breakpoint: Breakpoint,
        evidence_id: UUID,
        allowed_polarities: frozenset[str],
    ) -> Evidence | None:
        evidence = await self._session.get(Evidence, evidence_id)
        if (
            evidence is None
            or evidence.validation_status != "VALID"
            or evidence.invalidated_at is not None
            or evidence.polarity not in allowed_polarities
            or evidence.strength not in QUALIFYING_EVIDENCE_STRENGTHS
            or evidence.confidence < MIN_BREAKPOINT_EVIDENCE_CONFIDENCE
        ):
            return None
        interview = await self._session.get(InterviewSession, evidence.interview_session_id)
        if interview is None or interview.user_id != breakpoint.user_id:
            return None
        if not await self._evidence_targets_boundary(
            evidence_id=evidence.id,
            concept_id=breakpoint.concept_id,
            skill_dimension_id=breakpoint.skill_dimension_id,
        ):
            return None
        if not await self._demonstrates_reasoning_or_application(evidence.id):
            return None
        return evidence

    async def _demonstrates_reasoning_or_application(self, evidence_id: UUID) -> bool:
        rows = (
            await self._session.execute(
                select(EvidenceSource.source_role, InterviewEvent)
                .join(InterviewEvent, InterviewEvent.id == EvidenceSource.interview_event_id)
                .where(EvidenceSource.evidence_id == evidence_id)
                .order_by(InterviewEvent.server_sequence)
            )
        ).all()
        for source_role, event in rows:
            admission = evidence_source_admission(
                event_type=event.event_type,
                event_source=event.source,
                source_role=source_role,
            )
            if not admission.counts_as_candidate_demonstration:
                continue
            if event.event_type in {"TRANSCRIPT_FINALIZED", "MEANINGFUL_CODE_CHANGE"}:
                return True
            if (
                event.event_type == "CODE_SNAPSHOT_CREATED"
                and event.payload.get("trigger") != "INITIAL_EDITOR_STATE"
            ):
                return True
        return False

    async def _persist_relationship(
        self,
        *,
        breakpoint_id: UUID,
        evidence_id: UUID,
        relationship: str,
    ) -> bool:
        existing = await self._session.scalar(
            select(BreakpointEvidence).where(
                BreakpointEvidence.breakpoint_id == breakpoint_id,
                BreakpointEvidence.evidence_id == evidence_id,
            )
        )
        if existing is not None:
            return existing.relationship == relationship
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
        persisted = await self._session.scalar(
            select(BreakpointEvidence.relationship).where(
                BreakpointEvidence.breakpoint_id == breakpoint_id,
                BreakpointEvidence.evidence_id == evidence_id,
            )
        )
        return persisted == relationship

    async def _evidence_targets_boundary(
        self,
        *,
        evidence_id: UUID,
        concept_id: UUID,
        skill_dimension_id: UUID,
    ) -> bool:
        concept_match = await self._session.scalar(
            select(EvidenceConcept.evidence_id).where(
                EvidenceConcept.evidence_id == evidence_id,
                EvidenceConcept.concept_id == concept_id,
            )
        )
        if concept_match is None:
            return False
        skill_match = await self._session.scalar(
            select(EvidenceSkill.evidence_id).where(
                EvidenceSkill.evidence_id == evidence_id,
                EvidenceSkill.skill_dimension_id == skill_dimension_id,
            )
        )
        return skill_match is not None


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

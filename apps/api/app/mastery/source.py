"""Mastery-specific canonical source loading; projections and report prose are excluded."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence.models import (
    Assessment,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
    SkillDimension,
)
from app.evidence.source_admission import (
    EvidenceSourceCategory,
    evidence_source_admission,
)
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewConfiguration,
    InterviewerPrompt,
    InterviewSession,
)
from app.mastery.models import (
    ConceptMastery,
    ConceptMasteryEvidence,
    RetestAttemptEvidence,
    SkillMastery,
    SkillMasteryEvidence,
)
from app.mastery.policy import (
    BreakpointStatus,
    EvidenceIndependence,
    EvidencePolarity,
    EvidenceStrength,
    InterviewLevel,
    InterviewMode,
    MasteryBreakpointFact,
    MasteryEvidenceFact,
    MasteryTargetFacts,
)
from app.mastery.target_level import MasteryTargetLevelResolver
from app.observation.models import CodeSnapshot, InterviewEvent
from app.problems.models import Concept, ProblemVersion

TargetFamily = Literal["CONCEPT", "SKILL"]


@dataclass(frozen=True, slots=True)
class MasteryTargetSource:
    family: TargetFamily
    target_id: UUID
    canonical_key: str
    display_name: str
    category: str
    parent_concept_id: UUID | None
    parent_canonical_key: str | None
    parent_display_name: str | None
    facts: MasteryTargetFacts


@dataclass(frozen=True, slots=True)
class MasterySourceBundle:
    user_id: UUID
    target_level: Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
    targets: tuple[MasteryTargetSource, ...]


@dataclass(frozen=True, slots=True)
class _EvidenceRow:
    evidence: Evidence
    interview: InterviewSession
    configuration: InterviewConfiguration
    problem: ProblemVersion
    response: CandidateResponse | None
    prompt: InterviewerPrompt | None


@dataclass(frozen=True, slots=True)
class _MasterySourceEvent:
    event: InterviewEvent
    source_role: str
    category: EvidenceSourceCategory
    counts_as_candidate_demonstration: bool


class MasterySourceBuilder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(
        self,
        user_id: UUID,
        *,
        target_level: Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"] | None = None,
        admitted_only: bool = False,
    ) -> MasterySourceBundle:
        rows = await self._evidence_rows(user_id)
        resolved_level: InterviewLevel = target_level or await MasteryTargetLevelResolver(
            self._session
        ).resolve(user_id)
        evidence_ids = [row.evidence.id for row in rows]
        source_events = await self._source_events(evidence_ids)
        self_correction_ids = await self._self_correction_evidence_ids(rows, source_events)
        retest_ids = await self._retest_evidence_ids(evidence_ids)
        concept_links, skill_links = await self._target_links(evidence_ids)
        concept_admissions, skill_admissions = (
            await self._mastery_admissions(user_id) if admitted_only else (None, None)
        )
        concepts = await self._concepts(user_id, concept_links)
        parent_metadata = await self._parent_metadata(concepts)
        skills = await self._skills(user_id, skill_links)
        breakpoints = await self._breakpoints(user_id)

        rows_by_id = {row.evidence.id: row for row in rows}
        concept_families: dict[UUID, str] = {}
        for concept in concepts.values():
            parent_key = (
                parent_metadata[concept.parent_concept_id].canonical_key
                if concept.parent_concept_id in parent_metadata
                else None
            )
            concept_families[concept.id] = parent_key or concept.canonical_key
        evidence_concept_families: dict[UUID, str] = {}
        for evidence_id, target_ids in concept_links.items():
            evidence_concept_families[evidence_id] = "+".join(
                sorted({concept_families.get(item, str(item)) for item in target_ids})
            )

        concept_facts: dict[UUID, list[MasteryEvidenceFact]] = defaultdict(list)
        skill_facts: dict[UUID, list[MasteryEvidenceFact]] = defaultdict(list)
        for evidence_id, row in rows_by_id.items():
            events = source_events.get(evidence_id, ())
            family_key = evidence_concept_families.get(evidence_id, "cross_concept")
            for concept_id in concept_links.get(evidence_id, ()):
                if concept_admissions is not None and (
                    concept_id,
                    evidence_id,
                ) not in concept_admissions:
                    continue
                concept_facts[concept_id].append(
                    _fact(
                        row,
                        events,
                        retest_ids,
                        self_correction_ids,
                        concept_families.get(concept_id, family_key),
                    )
                )
            for skill_id in skill_links.get(evidence_id, ()):
                if skill_admissions is not None and (skill_id, evidence_id) not in skill_admissions:
                    continue
                skill_facts[skill_id].append(
                    _fact(
                        row,
                        events,
                        retest_ids,
                        self_correction_ids,
                        family_key,
                    )
                )

        targets: list[MasteryTargetSource] = []
        for concept in concepts.values():
            facts = tuple(concept_facts.get(concept.id, ()))
            target_breakpoints = _breakpoint_facts(
                breakpoints, concept_id=concept.id, skill_id=None
            )
            targets.append(
                MasteryTargetSource(
                    family="CONCEPT",
                    target_id=concept.id,
                    canonical_key=concept.canonical_key,
                    display_name=concept.display_name,
                    category=concept.category,
                    parent_concept_id=concept.parent_concept_id,
                    parent_canonical_key=(
                        parent_metadata[concept.parent_concept_id].canonical_key
                        if concept.parent_concept_id in parent_metadata
                        else None
                    ),
                    parent_display_name=(
                        parent_metadata[concept.parent_concept_id].display_name
                        if concept.parent_concept_id in parent_metadata
                        else None
                    ),
                    facts=MasteryTargetFacts(
                        target_family="CONCEPT",
                        target_level=resolved_level,
                        evidence=facts,
                        breakpoints=target_breakpoints,
                    ),
                )
            )
        for skill in skills.values():
            targets.append(
                MasteryTargetSource(
                    family="SKILL",
                    target_id=skill.id,
                    canonical_key=skill.canonical_key,
                    display_name=skill.display_name,
                    category="INTERVIEW_SKILL",
                    parent_concept_id=None,
                    parent_canonical_key=None,
                    parent_display_name=None,
                    facts=MasteryTargetFacts(
                        target_family="SKILL",
                        target_level=resolved_level,
                        evidence=tuple(skill_facts.get(skill.id, ())),
                        breakpoints=_breakpoint_facts(
                            breakpoints, concept_id=None, skill_id=skill.id
                        ),
                    ),
                )
            )
        targets.sort(key=lambda item: (item.family, item.canonical_key))
        return MasterySourceBundle(user_id, resolved_level, tuple(targets))

    async def _evidence_rows(self, user_id: UUID) -> tuple[_EvidenceRow, ...]:
        result = await self._session.execute(
            select(
                Evidence,
                InterviewSession,
                InterviewConfiguration,
                ProblemVersion,
                CandidateResponse,
                InterviewerPrompt,
            )
            .join(InterviewSession, InterviewSession.id == Evidence.interview_session_id)
            .join(
                InterviewConfiguration,
                InterviewConfiguration.id == InterviewSession.interview_configuration_id,
            )
            .join(ProblemVersion, ProblemVersion.id == InterviewSession.problem_version_id)
            .join(Assessment, Assessment.id == Evidence.originating_assessment_id)
            .outerjoin(
                CandidateResponse,
                CandidateResponse.id == Assessment.candidate_response_id,
            )
            .outerjoin(
                InterviewerPrompt,
                InterviewerPrompt.id == CandidateResponse.interviewer_prompt_id,
            )
            .where(InterviewSession.user_id == user_id)
            .where(Evidence.validation_status == "VALID", Evidence.invalidated_at.is_(None))
            .order_by(Evidence.created_at, Evidence.id)
        )
        return tuple(_EvidenceRow(*row) for row in result.all())

    async def _self_correction_evidence_ids(
        self,
        rows: tuple[_EvidenceRow, ...],
        source_events: dict[UUID, tuple[_MasterySourceEvent, ...]],
    ) -> frozenset[UUID]:
        response_ids = {row.response.id for row in rows if row.response is not None}
        if not response_ids:
            return frozenset()

        response_sources: dict[UUID, set[UUID]] = defaultdict(set)
        source_rows = await self._session.execute(
            select(
                CandidateResponseSource.candidate_response_id,
                CandidateResponseSource.interview_event_id,
            ).where(CandidateResponseSource.candidate_response_id.in_(response_ids))
        )
        for response_id, event_id in source_rows.tuples():
            response_sources[response_id].add(event_id)

        evidence_event_ids = {
            source.event.id
            for sources in source_events.values()
            for source in sources
            if source.counts_as_candidate_demonstration
        }
        snapshot_rows = await self._session.execute(
            select(CodeSnapshot.created_from_event_id, CodeSnapshot.id).where(
                CodeSnapshot.created_from_event_id.in_(evidence_event_ids)
            )
        )
        snapshots_by_creation_event = dict(snapshot_rows.tuples().all())

        result: set[UUID] = set()
        for row in rows:
            if row.response is None:
                continue
            sources = source_events.get(row.evidence.id, ())
            structured_event_ids = {
                source.event.id
                for source in sources
                if source.counts_as_candidate_demonstration
            }.intersection(
                response_sources.get(row.response.id, set())
            )
            snapshot_ids = {
                snapshots_by_creation_event[event_id]
                for event_id in structured_event_ids
                if event_id in snapshots_by_creation_event
            }
            if _is_independent_self_correction(
                polarity=row.evidence.polarity,
                independence=row.evidence.independence_level,
                prompt_id=row.response.interviewer_prompt_id,
                structured_snapshot_ids=snapshot_ids,
            ):
                result.add(row.evidence.id)
        return frozenset(result)

    async def _source_events(
        self, evidence_ids: list[UUID]
    ) -> dict[UUID, tuple[_MasterySourceEvent, ...]]:
        if not evidence_ids:
            return {}
        rows = await self._session.execute(
            select(EvidenceSource.evidence_id, EvidenceSource.source_role, InterviewEvent)
            .join(InterviewEvent, InterviewEvent.id == EvidenceSource.interview_event_id)
            .where(EvidenceSource.evidence_id.in_(evidence_ids))
            .order_by(EvidenceSource.evidence_id, InterviewEvent.server_sequence)
        )
        grouped: dict[UUID, list[_MasterySourceEvent]] = defaultdict(list)
        for evidence_id, source_role, event in rows.all():
            admission = evidence_source_admission(
                event_type=event.event_type,
                event_source=event.source,
                source_role=source_role,
            )
            grouped[evidence_id].append(
                _MasterySourceEvent(
                    event,
                    source_role,
                    admission.category,
                    admission.counts_as_candidate_demonstration,
                )
            )
        return {key: tuple(value) for key, value in grouped.items()}

    async def _retest_evidence_ids(self, evidence_ids: list[UUID]) -> frozenset[UUID]:
        if not evidence_ids:
            return frozenset()
        values = await self._session.scalars(
            select(RetestAttemptEvidence.evidence_id).where(
                RetestAttemptEvidence.evidence_id.in_(evidence_ids)
            )
        )
        return frozenset(values)

    async def _target_links(
        self, evidence_ids: list[UUID]
    ) -> tuple[dict[UUID, tuple[UUID, ...]], dict[UUID, tuple[UUID, ...]]]:
        concept_links: dict[UUID, list[UUID]] = defaultdict(list)
        skill_links: dict[UUID, list[UUID]] = defaultdict(list)
        if evidence_ids:
            for evidence_id, concept_id in (
                await self._session.execute(
                    select(EvidenceConcept.evidence_id, EvidenceConcept.concept_id).where(
                        EvidenceConcept.evidence_id.in_(evidence_ids)
                    )
                )
            ).all():
                concept_links[evidence_id].append(concept_id)
            for evidence_id, skill_id in (
                await self._session.execute(
                    select(EvidenceSkill.evidence_id, EvidenceSkill.skill_dimension_id).where(
                        EvidenceSkill.evidence_id.in_(evidence_ids)
                    )
                )
            ).all():
                skill_links[evidence_id].append(skill_id)
        return (
            {key: tuple(value) for key, value in concept_links.items()},
            {key: tuple(value) for key, value in skill_links.items()},
        )

    async def _mastery_admissions(
        self,
        user_id: UUID,
    ) -> tuple[set[tuple[UUID, UUID]], set[tuple[UUID, UUID]]]:
        concept_rows = await self._session.execute(
            select(
                ConceptMasteryEvidence.concept_id,
                ConceptMasteryEvidence.evidence_id,
            ).where(ConceptMasteryEvidence.user_id == user_id)
        )
        skill_rows = await self._session.execute(
            select(
                SkillMasteryEvidence.skill_dimension_id,
                SkillMasteryEvidence.evidence_id,
            ).where(SkillMasteryEvidence.user_id == user_id)
        )
        return set(concept_rows.tuples()), set(skill_rows.tuples())

    async def _concepts(
        self, user_id: UUID, links: dict[UUID, tuple[UUID, ...]]
    ) -> dict[UUID, Concept]:
        linked_ids = {item for values in links.values() for item in values}
        current_ids = set(
            await self._session.scalars(
                select(ConceptMastery.concept_id).where(ConceptMastery.user_id == user_id)
            )
        )
        ids = linked_ids | current_ids
        if not ids:
            return {}
        values = await self._session.scalars(select(Concept).where(Concept.id.in_(ids)))
        return {item.id: item for item in values}

    async def _skills(
        self, user_id: UUID, links: dict[UUID, tuple[UUID, ...]]
    ) -> dict[UUID, SkillDimension]:
        linked_ids = {item for values in links.values() for item in values}
        current_ids = set(
            await self._session.scalars(
                select(SkillMastery.skill_dimension_id).where(SkillMastery.user_id == user_id)
            )
        )
        ids = linked_ids | current_ids
        if not ids:
            return {}
        values = await self._session.scalars(
            select(SkillDimension).where(SkillDimension.id.in_(ids))
        )
        return {item.id: item for item in values}

    async def _parent_metadata(self, concepts: dict[UUID, Concept]) -> dict[UUID, Concept]:
        ids = {item.parent_concept_id for item in concepts.values() if item.parent_concept_id}
        if not ids:
            return {}
        values = await self._session.scalars(select(Concept).where(Concept.id.in_(ids)))
        return {item.id: item for item in values}

    async def _breakpoints(self, user_id: UUID) -> tuple[tuple[Breakpoint, frozenset[UUID]], ...]:
        rows = await self._session.execute(
            select(Breakpoint, BreakpointEvidence.evidence_id)
            .outerjoin(BreakpointEvidence, BreakpointEvidence.breakpoint_id == Breakpoint.id)
            .where(Breakpoint.user_id == user_id)
        )
        grouped: dict[UUID, tuple[Breakpoint, set[UUID]]] = {}
        for breakpoint, evidence_id in rows.all():
            current = grouped.setdefault(breakpoint.id, (breakpoint, set()))
            if evidence_id is not None:
                current[1].add(evidence_id)
        return tuple((item, frozenset(ids)) for item, ids in grouped.values())


def _breakpoint_facts(
    rows: tuple[tuple[Breakpoint, frozenset[UUID]], ...],
    *,
    concept_id: UUID | None,
    skill_id: UUID | None,
) -> tuple[MasteryBreakpointFact, ...]:
    result = []
    for breakpoint, evidence_ids in rows:
        if concept_id is not None and breakpoint.concept_id != concept_id:
            continue
        if skill_id is not None and breakpoint.skill_dimension_id != skill_id:
            continue
        result.append(
            MasteryBreakpointFact(
                breakpoint.id,
                cast(BreakpointStatus, breakpoint.status),
                breakpoint.severity,
                breakpoint.first_detected_at,
                evidence_ids,
            )
        )
    return tuple(result)


def _fact(
    row: _EvidenceRow,
    sources: tuple[_MasterySourceEvent, ...],
    retest_ids: frozenset[UUID],
    self_correction_ids: frozenset[UUID],
    concept_family_key: str,
) -> MasteryEvidenceFact:
    candidate_events = tuple(
        source.event for source in sources if source.counts_as_candidate_demonstration
    )
    prompt_kind = row.prompt.kind if row.prompt else None
    probe_strategy = row.prompt.probe_strategy if row.prompt else None
    demonstration_form = _demonstration_form(candidate_events, probe_strategy)
    context_key = _semantic_context_key(
        problem_id=row.problem.problem_id,
        demonstration_form=demonstration_form,
        probe_strategy=probe_strategy,
    )
    observation_key = _identity(
        "candidate-observation-v1",
        *(str(item.id) for item in candidate_events),
    )
    application = _demonstrates_reasoning_or_application(candidate_events)
    return MasteryEvidenceFact(
        evidence_id=row.evidence.id,
        session_id=row.interview.id,
        problem_version_id=row.problem.id,
        problem_id=row.problem.problem_id,
        occurred_at=row.evidence.created_at,
        polarity=cast(EvidencePolarity, row.evidence.polarity),
        strength=cast(EvidenceStrength, row.evidence.strength),
        independence=cast(EvidenceIndependence, row.evidence.independence_level),
        evidence_type=row.evidence.evidence_type,
        interview_mode=cast(InterviewMode, row.configuration.mode),
        interview_level=cast(InterviewLevel, row.configuration.level),
        context_key=context_key,
        observation_key=observation_key,
        concept_family_key=concept_family_key,
        demonstration_form=demonstration_form,
        prompt_kind=prompt_kind,
        probe_strategy=probe_strategy,
        is_retest=row.evidence.id in retest_ids,
        is_self_correction=row.evidence.id in self_correction_ids,
        demonstrates_reasoning_or_application=application,
        problem_title=row.problem.title,
        finding=row.evidence.finding,
    )


def _is_independent_self_correction(
    *,
    polarity: str,
    independence: str,
    prompt_id: UUID | None,
    structured_snapshot_ids: set[UUID],
) -> bool:
    """Require the conservative canonical before/after correction boundary."""

    return bool(
        polarity in {"POSITIVE", "MIXED"}
        and independence == "INDEPENDENT"
        and prompt_id is None
        and len(structured_snapshot_ids) == 2
    )


def _demonstrates_reasoning_or_application(
    events: tuple[InterviewEvent, ...],
) -> bool:
    return any(
        event.event_type == "TRANSCRIPT_FINALIZED"
        or event.event_type == "MEANINGFUL_CODE_CHANGE"
        or (
            event.event_type == "CODE_SNAPSHOT_CREATED"
            and event.payload.get("trigger") != "INITIAL_EDITOR_STATE"
        )
        for event in events
    )


def _demonstration_form(
    events: tuple[InterviewEvent, ...], probe_strategy: str | None
) -> str:
    event_types = {event.event_type for event in events}
    meaningful_code = _demonstrates_reasoning_or_application(
        tuple(
            event
            for event in events
            if event.event_type in {"CODE_SNAPSHOT_CREATED", "MEANINGFUL_CODE_CHANGE"}
        )
    )
    has_reasoning = "TRANSCRIPT_FINALIZED" in event_types
    has_execution = bool(
        event_types.intersection({"RUN_CLICKED", "COMPILE_COMPLETED", "TEST_COMPLETED"})
    )
    if probe_strategy in {"TRANSFER", "CONSTRAINT_MUTATION"}:
        return probe_strategy
    if meaningful_code and has_execution:
        return "DEBUGGING_APPLICATION"
    if meaningful_code and has_reasoning:
        return "EXPLANATION_AND_IMPLEMENTATION"
    if meaningful_code:
        return "IMPLEMENTATION"
    if has_reasoning:
        return "EXPLANATION"
    if has_execution:
        return "EXECUTION_ONLY"
    return "OTHER_OBSERVATION"


def _semantic_context_key(
    *,
    problem_id: UUID,
    demonstration_form: str,
    probe_strategy: str | None,
) -> str:
    diagnostic_strategy = probe_strategy or "NO_DIAGNOSTIC_STRATEGY"
    return _identity(
        "semantic-context-v2",
        str(problem_id),
        demonstration_form,
        diagnostic_strategy,
    )


def _identity(namespace: str, *parts: str) -> str:
    normalized = "\x1f".join((namespace, *parts)).encode()
    return "sha256:" + hashlib.sha256(normalized).hexdigest()

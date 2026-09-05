"""Development-only canonical fixtures projected by the production CounterMap path."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid5

from app.countermap.source import (
    BreakpointEvidenceLink,
    CanonicalBreakpointSource,
    CanonicalEvidenceSource,
    ClaimSource,
    CodeSnapshotSource,
    CounterMapSourceBundle,
    DecisionSource,
    DeliverySource,
    EventSource,
    EvidenceSourceLink,
    EvidenceTarget,
    ExecutionSource,
    ResponseSource,
    TranscriptSource,
)

NAMESPACE = UUID("7a000000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class DevelopmentCounterMapFixture:
    fixture_id: str
    label: str
    description: str
    bundle: CounterMapSourceBundle


def load_development_countermap_fixtures() -> tuple[DevelopmentCounterMapFixture, ...]:
    return (
        DevelopmentCounterMapFixture(
            "simulation-success-and-misconception",
            "Simulation",
            "An independent defense, a later misconception, and the breakpoint it exposed.",
            _simulation(),
        ),
        DevelopmentCounterMapFixture(
            "coach-assisted-improvement-open-breakpoint",
            "Coach",
            "Guidance materially changed the next response without pretending it proved mastery.",
            _coach(),
        ),
        DevelopmentCounterMapFixture(
            "delivery-and-self-correction-integrity",
            "Integrity",
            "Only delivered words and structured before/after correction provenance survive.",
            _integrity(),
        ),
    )


def _simulation() -> CounterMapSourceBundle:
    key = "simulation"
    session_id = _id(key, "session")
    concept = _target(key, "hash-tables", "Hash tables")
    skill = _target(key, "complexity-reasoning", "Complexity reasoning")
    events = [
        _event(key, session_id, 1, "TRANSCRIPT_FINALIZED", "CANDIDATE_VOICE"),
        _event(key, session_id, 2, "COUNTERQ_UTTERANCE_DELIVERED", "COUNTERQ_VOICE"),
        _event(key, session_id, 3, "TRANSCRIPT_FINALIZED", "CANDIDATE_VOICE"),
        _event(key, session_id, 4, "CODE_SNAPSHOT_CREATED", "NATIVE_EDITOR"),
        _event(key, session_id, 5, "RUN_CLICKED", "NATIVE_RUNNER"),
        _event(key, session_id, 6, "TRANSCRIPT_FINALIZED", "CANDIDATE_VOICE"),
    ]
    prompt_id = _id(key, "prompt")
    delivery_id = _id(key, "delivery")
    response_id = _id(key, "response")
    claim_id = _id(key, "claim-approach")
    negative_id = _id(key, "evidence-negative")
    return CounterMapSourceBundle(
        interview_session_id=session_id,
        mode="SIMULATION",
        source_watermark=6,
        events=events,
        transcripts=[
            _transcript(
                key, session_id, events[0], "CANDIDATE", "I will store complements in a map."
            ),
            _transcript(
                key,
                session_id,
                events[1],
                "COUNTERQ",
                "Why do you check before inserting the current value?",
                delivery_state="DELIVERED",
            ),
            _transcript(
                key,
                session_id,
                events[2],
                "CANDIDATE",
                "Checking first prevents one index from matching itself.",
            ),
            _transcript(
                key,
                session_id,
                events[5],
                "CANDIDATE",
                "Hash lookups are always constant time.",
            ),
        ],
        claims=[
            ClaimSource(
                id=claim_id,
                claim_type="ALGORITHM_CHOICE",
                normalized_claim="Use a complement map.",
                verbatim_excerpt="I will store complements in a map.",
                source_event_id=events[0].id,
                source_server_sequence=1,
                source_code_snapshot_id=None,
            ),
            ClaimSource(
                id=_id(key, "claim-complexity"),
                claim_type="COMPLEXITY",
                normalized_claim="Hash lookup is always constant time.",
                verbatim_excerpt="Hash lookups are always constant time.",
                source_event_id=events[5].id,
                source_server_sequence=6,
                source_code_snapshot_id=None,
            ),
        ],
        responses=[
            ResponseSource(
                id=response_id,
                prompt_id=prompt_id,
                summary=None,
                source_event_ids=[events[2].id],
                start_sequence=3,
                end_sequence=3,
            )
        ],
        code_snapshots=[_snapshot(key, session_id, events[3], 1)],
        code_diffs=[],
        executions=[
            ExecutionSource(
                id=_id(key, "run"),
                run_event_id=events[4].id,
                code_snapshot_id=_id(key, "snapshot-1"),
                server_sequence=5,
                status="SUCCEEDED",
                language="python",
                visible_passed=3,
                visible_failed=0,
            )
        ],
        decisions=[
            DecisionSource(
                id=_id(key, "decision"),
                status="AUTHORIZED",
                action="PROBE",
                target_claim_id=claim_id,
                target_event_id=events[0].id,
                target_code_snapshot_id=None,
                source_event_watermark=1,
            )
        ],
        deliveries=[
            DeliverySource(
                id=delivery_id,
                prompt_id=prompt_id,
                prompt_status="ANSWERED",
                prompt_kind="PROBE",
                prompt_origin="EXAMINER_DECISION",
                probe_strategy="WHY",
                examiner_decision_id=_id(key, "decision"),
                target_claim_id=claim_id,
                target_event_id=events[0].id,
                source_code_snapshot_id=None,
                target_concept_id=concept.id,
                target_skill_dimension_id=skill.id,
                assistance_type=None,
                hint_level=None,
                actual_transcript_segment_id=_id(key, "transcript-2"),
                actual_event_id=events[1].id,
                actual_text="Why do you check before inserting the current value?",
                intended_text="Why do you check before inserting the current value?",
                delivery_state="DELIVERED",
                server_sequence=2,
                stage="APPROACH_DEFENSE",
            )
        ],
        evidence=[
            _evidence(
                key,
                "positive",
                events[2],
                concept,
                skill,
                "POSITIVE",
                "STRONG",
                "INDEPENDENT",
                "The complement-before-insertion invariant was defended independently.",
                candidate_response_id=response_id,
            ),
            _evidence(
                key,
                "negative",
                events[5],
                concept,
                skill,
                "NEGATIVE",
                "MODERATE",
                "INDEPENDENT",
                "The complexity claim omitted worst-case hash behavior.",
            ),
            _evidence(
                key,
                "mixed",
                events[4],
                concept,
                skill,
                "MIXED",
                "MODERATE",
                "AFTER_PROBE",
                "Visible tests passed while the complexity boundary remained uncertain.",
                source_code_snapshot_id=_id(key, "snapshot-1"),
            ),
        ],
        breakpoints=[
            CanonicalBreakpointSource(
                id=_id(key, "breakpoint"),
                status="OPEN",
                severity="MEDIUM",
                summary="Worst-case hash lookup remains an open reasoning boundary.",
                concept_target=concept,
                skill_target=skill,
                evidence_links=[
                    BreakpointEvidenceLink(evidence_id=negative_id, relationship="CREATED")
                ],
            )
        ],
    )


def _coach() -> CounterMapSourceBundle:
    key = "coach"
    session_id = _id(key, "session")
    concept = _target(key, "hash-tables", "Hash tables")
    skill = _target(key, "complexity-reasoning", "Complexity reasoning")
    events = [
        _event(key, session_id, 1, "TRANSCRIPT_FINALIZED", "CANDIDATE_VOICE"),
        _event(key, session_id, 2, "COUNTERQ_UTTERANCE_DELIVERED", "COUNTERQ_VOICE"),
        _event(key, session_id, 3, "TRANSCRIPT_FINALIZED", "CANDIDATE_VOICE"),
    ]
    claim_id = _id(key, "claim")
    prompt_id = _id(key, "prompt")
    delivery_id = _id(key, "delivery")
    response_id = _id(key, "response")
    negative_id = _id(key, "evidence-negative")
    positive_id = _id(key, "evidence-positive")
    return CounterMapSourceBundle(
        interview_session_id=session_id,
        mode="COACH",
        source_watermark=3,
        events=events,
        transcripts=[
            _transcript(key, session_id, events[0], "CANDIDATE", "Hash lookup is always O(1)."),
            _transcript(
                key,
                session_id,
                events[1],
                "COUNTERQ",
                "Separate the expected case from the worst case.",
                delivery_state="DELIVERED",
            ),
            _transcript(
                key,
                session_id,
                events[2],
                "CANDIDATE",
                "Expected lookup is O(1), but collisions can make the worst case O(n).",
            ),
        ],
        claims=[
            ClaimSource(
                id=claim_id,
                claim_type="COMPLEXITY",
                normalized_claim="Hash lookup is always constant time.",
                verbatim_excerpt="Hash lookup is always O(1).",
                source_event_id=events[0].id,
                source_server_sequence=1,
                source_code_snapshot_id=None,
            )
        ],
        responses=[
            ResponseSource(
                id=response_id,
                prompt_id=prompt_id,
                summary=None,
                source_event_ids=[events[2].id],
                start_sequence=3,
                end_sequence=3,
            )
        ],
        code_snapshots=[],
        code_diffs=[],
        executions=[],
        decisions=[],
        deliveries=[
            DeliverySource(
                id=delivery_id,
                prompt_id=prompt_id,
                prompt_status="ANSWERED",
                prompt_kind="INSTRUCTION",
                prompt_origin="SYSTEM",
                probe_strategy=None,
                examiner_decision_id=None,
                target_claim_id=claim_id,
                target_event_id=events[0].id,
                source_code_snapshot_id=None,
                target_concept_id=concept.id,
                target_skill_dimension_id=skill.id,
                assistance_type="CONCEPTUAL_HINT",
                hint_level="CONCEPTUAL_HINT",
                actual_transcript_segment_id=_id(key, "transcript-2"),
                actual_event_id=events[1].id,
                actual_text="Separate the expected case from the worst case.",
                intended_text="Separate the expected case from the worst case.",
                delivery_state="DELIVERED",
                server_sequence=2,
                stage="COMPLEXITY_EDGE_CASES",
            )
        ],
        evidence=[
            _evidence(
                key,
                "negative",
                events[0],
                concept,
                skill,
                "NEGATIVE",
                "MODERATE",
                "INDEPENDENT",
                "The initial complexity claim omitted the worst-case boundary.",
            ),
            _evidence(
                key,
                "positive",
                events[2],
                concept,
                skill,
                "POSITIVE",
                "MODERATE",
                "AFTER_LIGHT_GUIDANCE",
                "The expected and worst cases were distinguished after light guidance.",
                candidate_response_id=response_id,
            ),
        ],
        breakpoints=[
            CanonicalBreakpointSource(
                id=_id(key, "breakpoint"),
                status="OPEN",
                severity="MEDIUM",
                summary="Complexity reasoning still needs independent verification.",
                concept_target=concept,
                skill_target=skill,
                evidence_links=[
                    BreakpointEvidenceLink(evidence_id=negative_id, relationship="CREATED"),
                    BreakpointEvidenceLink(
                        evidence_id=positive_id,
                        relationship="RESOLUTION_SUPPORT",
                    ),
                ],
            )
        ],
    )


def _integrity() -> CounterMapSourceBundle:
    key = "integrity"
    session_id = _id(key, "session")
    concept = _target(key, "window-boundary", "Sliding window boundary")
    skill = _target(key, "debugging", "Debugging")
    events = [
        _event(key, session_id, 1, "CODE_SNAPSHOT_CREATED", "NATIVE_EDITOR"),
        _event(key, session_id, 2, "MEANINGFUL_CODE_CHANGE", "NATIVE_EDITOR"),
        _event(key, session_id, 3, "MEANINGFUL_CODE_CHANGE", "NATIVE_EDITOR"),
        _event(key, session_id, 4, "COUNTERQ_UTTERANCE_DELIVERED", "COUNTERQ_VOICE"),
    ]
    evidence_id = _id(key, "evidence-correction")
    snapshots = [
        _snapshot(key, session_id, events[0], 1),
        _snapshot(key, session_id, events[1], 2, parent=1),
        _snapshot(key, session_id, events[2], 5, parent=2),
    ]
    return CounterMapSourceBundle(
        interview_session_id=session_id,
        mode="SIMULATION",
        source_watermark=4,
        events=events,
        transcripts=[
            _transcript(
                key,
                session_id,
                events[3],
                "COUNTERQ",
                "What invariant",
                delivery_state="INTERRUPTED",
            )
        ],
        claims=[],
        responses=[
            ResponseSource(
                id=_id(key, "correction-response"),
                prompt_id=None,
                summary="The candidate revised the implementation independently.",
                source_event_ids=[events[0].id, events[1].id],
                start_sequence=1,
                end_sequence=2,
            )
        ],
        code_snapshots=snapshots,
        code_diffs=[],
        executions=[],
        decisions=[
            DecisionSource(
                id=_id(key, "decision"),
                status="AUTHORIZED",
                action="PROBE",
                target_claim_id=None,
                target_event_id=events[1].id,
                target_code_snapshot_id=snapshots[1].id,
                source_event_watermark=2,
            )
        ],
        deliveries=[
            DeliverySource(
                id=_id(key, "delivery"),
                prompt_id=_id(key, "prompt"),
                prompt_status="INTERRUPTED",
                prompt_kind="PROBE",
                prompt_origin="EXAMINER_DECISION",
                probe_strategy="PROVE",
                examiner_decision_id=_id(key, "decision"),
                target_claim_id=None,
                target_event_id=events[1].id,
                source_code_snapshot_id=snapshots[1].id,
                target_concept_id=concept.id,
                target_skill_dimension_id=skill.id,
                assistance_type=None,
                hint_level=None,
                actual_transcript_segment_id=_id(key, "transcript-4"),
                actual_event_id=events[3].id,
                actual_text="What invariant",
                intended_text="What invariant proves the window never moves backward?",
                delivery_state="INTERRUPTED",
                server_sequence=4,
                stage="IMPLEMENTATION",
            )
        ],
        evidence=[
            CanonicalEvidenceSource(
                id=evidence_id,
                evidence_type="CORRECTNESS",
                polarity="MIXED",
                strength="STRONG",
                independence_level="INDEPENDENT",
                finding="The candidate self-corrected the stale window boundary independently.",
                source_links=[
                    EvidenceSourceLink(
                        event_id=events[0].id, server_sequence=1, source_role="CONTEXT"
                    ),
                    EvidenceSourceLink(
                        event_id=events[1].id, server_sequence=2, source_role="PRIMARY"
                    ),
                ],
                concept_targets=[concept],
                skill_targets=[skill],
                originating_assessment_id=_id(key, "assessment-correction"),
                candidate_response_id=_id(key, "correction-response"),
                source_code_snapshot_id=snapshots[1].id,
            )
        ],
        breakpoints=[],
    )


def _event(
    key: str,
    session_id: UUID,
    sequence: int,
    event_type: str,
    source: str,
) -> EventSource:
    return EventSource(
        id=_id(key, f"event-{sequence}"),
        server_sequence=sequence,
        event_type=event_type,
        source=source,
        stage="IMPLEMENTATION" if sequence < 5 else "COMPLEXITY_EDGE_CASES",
        causation_id=None,
        correlation_id=None,
        code_snapshot_id=None,
        payload={},
    )


def _transcript(
    key: str,
    session_id: UUID,
    event: EventSource,
    speaker: str,
    text: str,
    *,
    delivery_state: str | None = None,
) -> TranscriptSource:
    del session_id
    return TranscriptSource(
        id=_id(key, f"transcript-{event.server_sequence}"),
        event_id=event.id,
        server_sequence=event.server_sequence,
        speaker=speaker,  # type: ignore[arg-type]
        text=text,
        stage=event.stage or "IMPLEMENTATION",
        delivery_state=delivery_state,
    )


def _snapshot(
    key: str,
    session_id: UUID,
    event: EventSource,
    version: int,
    *,
    parent: int | None = None,
) -> CodeSnapshotSource:
    del session_id
    source_code = development_source_code_for_key(key, version)
    return CodeSnapshotSource(
        id=_id(key, f"snapshot-{version}"),
        version=version,
        parent_snapshot_id=_id(key, f"snapshot-{parent}") if parent else None,
        language="python",
        content_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
        created_from_event_id=event.id,
        server_sequence=event.server_sequence,
        stage=event.stage,
    )


def development_source_code(fixture_id: str, version: int) -> str | None:
    key_by_fixture = {
        "simulation-success-and-misconception": "simulation",
        "coach-assisted-improvement-open-breakpoint": "coach",
        "delivery-and-self-correction-integrity": "integrity",
    }
    key = key_by_fixture.get(fixture_id)
    if key is None:
        return None
    try:
        return development_source_code_for_key(key, version)
    except KeyError:
        return None


def development_source_code_for_key(key: str, version: int) -> str:
    sources = {
        ("simulation", 1): (
            "def two_sum(values, target):\n"
            "    seen = {}\n"
            "    for index, value in enumerate(values):\n"
            "        complement = target - value\n"
            "        if complement in seen:\n"
            "            return [seen[complement], index]\n"
            "        seen[value] = index\n"
        ),
        ("integrity", 1): (
            "def longest_unique(text):\n"
            "    left = 0\n"
            "    best = 0\n"
            "    last = {}\n"
            "    for right, char in enumerate(text):\n"
            "        if char in last:\n"
            "            left = last[char] + 1\n"
            "        last[char] = right\n"
            "        best = max(best, right - left + 1)\n"
            "    return best\n"
        ),
        ("integrity", 2): (
            "def longest_unique(text):\n"
            "    left = 0\n"
            "    best = 0\n"
            "    last = {}\n"
            "    for right, char in enumerate(text):\n"
            "        if char in last:\n"
            "            left = max(left, last[char] + 1)\n"
            "        last[char] = right\n"
            "        best = max(best, right - left + 1)\n"
            "    return best\n"
        ),
        ("integrity", 5): (
            "def longest_unique(text):\n"
            "    left = best = 0\n"
            "    last = {}\n"
            "    for right, char in enumerate(text):\n"
            "        left = max(left, last.get(char, -1) + 1)\n"
            "        last[char] = right\n"
            "        best = max(best, right - left + 1)\n"
            "    return best\n"
        ),
    }
    return sources[(key, version)]


def _target(key: str, canonical_key: str, display_name: str) -> EvidenceTarget:
    return EvidenceTarget(
        id=_id(key, f"target-{canonical_key}"),
        canonical_key=canonical_key,
        display_name=display_name,
    )


def _evidence(
    key: str,
    suffix: str,
    event: EventSource,
    concept: EvidenceTarget,
    skill: EvidenceTarget,
    polarity: str,
    strength: str,
    independence: str,
    finding: str,
    *,
    candidate_response_id: UUID | None = None,
    source_code_snapshot_id: UUID | None = None,
) -> CanonicalEvidenceSource:
    return CanonicalEvidenceSource(
        id=_id(key, f"evidence-{suffix}"),
        evidence_type="CORRECTNESS",
        polarity=polarity,  # type: ignore[arg-type]
        strength=strength,  # type: ignore[arg-type]
        independence_level=independence,  # type: ignore[arg-type]
        finding=finding,
        source_links=[
            EvidenceSourceLink(
                event_id=event.id,
                server_sequence=event.server_sequence,
                source_role="PRIMARY",
            )
        ],
        concept_targets=[concept],
        skill_targets=[skill],
        originating_assessment_id=_id(key, f"assessment-{suffix}"),
        candidate_response_id=candidate_response_id,
        source_code_snapshot_id=source_code_snapshot_id,
    )


def _id(key: str, name: str) -> UUID:
    return uuid5(NAMESPACE, f"{key}:{name}")

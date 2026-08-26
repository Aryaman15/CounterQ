from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from test_stage1_1a_persistence import Stage1PersistenceGraph, add_event, create_stage1_graph

from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.ai_gateway.repository import AIInvocationRepository
from app.db.ids import uuid7
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.examiner.repository import ExaminerRepository
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import (
    CandidateResponse,
    InterviewerPrompt,
    InterviewerPromptDelivery,
)
from app.observation.models import CodeSnapshot, InterviewEvent, TranscriptSegment
from app.observation.repository import ObservationRepository


@dataclass(frozen=True)
class AIContext:
    policy: AIPolicyVersion
    invocation: AIInvocation


async def create_ai_context(
    db_session: AsyncSession,
    graph: Stage1PersistenceGraph,
    *,
    purpose: str = "CLAIM_EXTRACTION",
    now: datetime | None = None,
) -> AIContext:
    policy = AIPolicyVersion(
        policy_key=f"{purpose.lower()}-{uuid7()}",
        version="v1",
        configuration_json={"fixture": True},
    )
    db_session.add(policy)
    await db_session.flush()
    started_at = now or datetime.now(UTC)
    invocation = await AIInvocationRepository(db_session).add(
        user_id=graph.user.id,
        interview_session_id=graph.interview_session.id,
        ai_policy_version_id=policy.id,
        purpose=purpose,
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=200),
        estimated_cost=Decimal("0.001000"),
    )
    return AIContext(policy=policy, invocation=invocation)


async def add_transcript_segment(
    db_session: AsyncSession,
    graph: Stage1PersistenceGraph,
    *,
    server_sequence: int,
    text: str,
    speaker: str = "CANDIDATE",
) -> tuple[InterviewEvent, TranscriptSegment]:
    event_type = "TRANSCRIPT_FINALIZED"
    source = "CANDIDATE_VOICE"
    if speaker == "COUNTERQ":
        event_type = "COUNTERQ_UTTERANCE_DELIVERED"
        source = "COUNTERQ_VOICE"
    event = await add_event(
        db_session,
        graph,
        server_sequence=server_sequence,
        event_type=event_type,
        source=source,
    )
    now = datetime.now(UTC)
    segment = await ObservationRepository(db_session).add_transcript_segment(
        session_id=graph.interview_session.id,
        event_id=event.id,
        speaker=speaker,
        sequence=server_sequence,
        started_at=now,
        ended_at=now + timedelta(seconds=2),
        text=text,
        interview_stage="IMPLEMENTATION",
        interview_state_version=graph.interview_session.state_version,
        delivery_state="DELIVERED" if speaker == "COUNTERQ" else None,
    )
    return event, segment


async def add_snapshot(
    db_session: AsyncSession,
    graph: Stage1PersistenceGraph,
    *,
    server_sequence: int,
    version_number: int,
    source_code: str = "int main() { return 0; }",
    parent_snapshot_id: UUID | None = None,
    now: datetime | None = None,
) -> tuple[InterviewEvent, CodeSnapshot]:
    event = await add_event(
        db_session,
        graph,
        server_sequence=server_sequence,
        event_type="CODE_SNAPSHOT_CREATED",
        source="NATIVE_EDITOR",
        now=now,
    )
    snapshot = await ObservationRepository(db_session).add_code_snapshot(
        session_id=graph.interview_session.id,
        version_number=version_number,
        parent_snapshot_id=parent_snapshot_id,
        language="cpp",
        source_code=source_code,
        content_hash=f"sha256:{uuid7()}",
        created_from_event_id=event.id,
    )
    return event, snapshot


async def test_candidate_claim_can_reference_same_session_factual_provenance(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, graph)
    event, segment = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=1,
        text="I'll use unordered_map because lookup is always O(1).",
    )

    claim = await ExaminerRepository(db_session).add_candidate_claim(
        interview_session_id=graph.interview_session.id,
        origin_kind="TRANSCRIPT",
        source_transcript_segment_id=segment.id,
        source_event_id=event.id,
        verbatim_excerpt="lookup is always O(1)",
        normalized_claim="unordered_map lookup has guaranteed O(1) time complexity",
        claim_type="COMPLEXITY",
        extraction_confidence=Decimal("0.9200"),
        status="ACCEPTED_AS_INTERPRETATION",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )

    assert claim.interview_session_id == graph.interview_session.id
    assert claim.source_transcript_segment_id == segment.id
    assert claim.source_event_id == event.id


async def test_candidate_claim_rejects_cross_session_factual_provenance(
    db_session: AsyncSession,
) -> None:
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, session_a)
    _, segment_from_b = await add_transcript_segment(
        db_session,
        session_b,
        server_sequence=1,
        text="This belongs to another session.",
    )

    with pytest.raises(IntegrityError):
        await ExaminerRepository(db_session).add_candidate_claim(
            interview_session_id=session_a.interview_session.id,
            origin_kind="TRANSCRIPT",
            source_transcript_segment_id=segment_from_b.id,
            normalized_claim="cross-session transcript source",
            claim_type="CORRECTNESS",
            extraction_confidence=Decimal("0.5000"),
            status="PROPOSED",
            ai_invocation_id=ai.invocation.id,
            ai_policy_version_id=ai.policy.id,
        )


async def test_code_origin_claim_can_reference_exact_snapshot_and_code_diff(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, graph)
    _, first_snapshot = await add_snapshot(db_session, graph, server_sequence=1, version_number=1)
    diff_event, second_snapshot = await add_snapshot(
        db_session,
        graph,
        server_sequence=2,
        version_number=2,
        parent_snapshot_id=first_snapshot.id,
        source_code="int main() { return 1; }",
    )
    code_diff = await ObservationRepository(db_session).add_code_diff(
        session_id=graph.interview_session.id,
        from_snapshot_id=first_snapshot.id,
        to_snapshot_id=second_snapshot.id,
        diff_format="UNIFIED",
        diff_content="- return 0;\n+ return 1;",
        change_summary="Changed return value.",
        significance="MEANINGFUL",
        created_from_event_id=diff_event.id,
    )

    claim = await ExaminerRepository(db_session).add_candidate_claim(
        interview_session_id=graph.interview_session.id,
        origin_kind="CODE",
        source_code_snapshot_id=second_snapshot.id,
        source_code_diff_id=code_diff.id,
        normalized_claim="implementation changed a meaningful behavior",
        claim_type="IMPLEMENTATION",
        extraction_confidence=Decimal("0.8800"),
        status="ACCEPTED_AS_INTERPRETATION",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )

    assert claim.source_code_snapshot_id == second_snapshot.id
    assert claim.source_code_diff_id == code_diff.id


async def test_examiner_decision_retains_watermark_code_and_model_provenance(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")
    event, snapshot = await add_snapshot(db_session, graph, server_sequence=1, version_number=1)

    decision = await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="PROBE",
        target_event_id=event.id,
        target_code_snapshot_id=snapshot.id,
        proposed_probe_strategy="ASSUMPTION_CHALLENGE",
        technical_rationale="The current code context suggests an assumption worth testing.",
        confidence=Decimal("0.8300"),
        priority=7,
        urgency=5,
        source_event_watermark=event.server_sequence,
        source_state_version=graph.interview_session.state_version,
        deadline_at=datetime.now(UTC) + timedelta(seconds=5),
        expiry_policy="LIVE_PROMPT_WINDOW",
        policy_gate_outcome="AUTHORIZED",
        status="AUTHORIZED",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )

    assert decision.source_event_watermark == event.server_sequence
    assert decision.source_state_version == graph.interview_session.state_version
    assert decision.target_code_snapshot_id == snapshot.id
    assert decision.ai_invocation_id == ai.invocation.id
    assert decision.ai_policy_version_id == ai.policy.id


async def test_examiner_decision_rejects_cross_session_targets(
    db_session: AsyncSession,
) -> None:
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, session_a, purpose="LIVE_EXAMINER")
    _, snapshot_from_b = await add_snapshot(
        db_session,
        session_b,
        server_sequence=1,
        version_number=1,
    )

    with pytest.raises(IntegrityError):
        await ExaminerRepository(db_session).add_examiner_decision(
            interview_session_id=session_a.interview_session.id,
            action="PROBE",
            target_code_snapshot_id=snapshot_from_b.id,
            proposed_probe_strategy="EDGE_CASE",
            technical_rationale="This should be rejected as cross-session provenance.",
            source_event_watermark=0,
            source_state_version=0,
            status="PROPOSED",
            ai_invocation_id=ai.invocation.id,
            ai_policy_version_id=ai.policy.id,
        )


async def test_stale_examiner_decision_can_exist_without_prompt_or_delivery(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")
    decision = await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="OBSERVE",
        technical_rationale="Reasoning became stale before authorization.",
        source_event_watermark=0,
        source_state_version=graph.interview_session.state_version,
        policy_gate_outcome="STALE",
        policy_gate_reason="Candidate self-corrected before delivery.",
        status="STALE",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )

    prompt_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewerPrompt)
        .where(InterviewerPrompt.examiner_decision_id == decision.id),
    )
    delivery_count = await db_session.scalar(
        select(func.count()).select_from(InterviewerPromptDelivery)
    )

    assert prompt_count == 0
    assert delivery_count == 0


async def test_interviewer_prompt_origins_and_probe_strategy_constraints(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")
    decision = await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="PROBE",
        proposed_probe_strategy="ASSUMPTION_CHALLENGE",
        technical_rationale="Probe an assumption.",
        source_event_watermark=0,
        source_state_version=0,
        status="AUTHORIZED",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )
    interactions = InterviewInteractionRepository(db_session)
    probe_prompt = await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        examiner_decision_id=decision.id,
        origin="EXAMINER_DECISION",
        kind="PROBE",
        probe_strategy="ASSUMPTION_CHALLENGE",
        intent="Test whether the candidate's always claim survives scrutiny.",
        status="AUTHORIZED",
        authorized_at=datetime.now(UTC),
    )
    system_prompt = await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="STATE_MACHINE",
        kind="TIME_WARNING",
        intent="Warn the candidate that time is almost over.",
        status="AUTHORIZED",
        authorized_at=datetime.now(UTC),
    )

    assert probe_prompt.examiner_decision_id == decision.id
    assert system_prompt.examiner_decision_id is None

    with pytest.raises(IntegrityError):
        await interactions.add_prompt(
            interview_session_id=graph.interview_session.id,
            origin="EXAMINER_DECISION",
            kind="CLARIFICATION",
            probe_strategy="WHY",
            intent="Invalid hidden probe.",
            status="AUTHORIZED",
        )


async def test_prompt_delivery_same_session_and_interrupted_semantics(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    interactions = InterviewInteractionRepository(db_session)
    prompt = await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="CLARIFICATION",
        intent="Clarify a complexity claim.",
        status="AUTHORIZED",
        authorized_at=datetime.now(UTC),
    )
    _, actual_segment = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=1,
        text="You said always...",
        speaker="COUNTERQ",
    )
    now = datetime.now(UTC)
    delivery = await interactions.add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="You said always. Is that actually guaranteed?",
        actual_transcript_segment_id=actual_segment.id,
        delivery_state="INTERRUPTED",
        started_at=now,
        interrupted_at=now + timedelta(seconds=1),
    )
    retry = await interactions.add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=2,
        intended_text="Let me rephrase: is lookup always guaranteed constant time?",
        delivery_state="CANCELLED",
        started_at=now + timedelta(seconds=2),
    )

    assert delivery.intended_text.endswith("guaranteed?")
    assert delivery.actual_transcript_segment_id == actual_segment.id
    assert delivery.delivery_state == "INTERRUPTED"
    assert retry.delivery_attempt == 2


async def test_prompt_delivery_rejects_cross_session_prompt_or_transcript(
    db_session: AsyncSession,
) -> None:
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    prompt_b = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=session_b.interview_session.id,
        origin="SYSTEM",
        kind="TIME_WARNING",
        intent="Session B prompt.",
        status="AUTHORIZED",
    )

    with pytest.raises(IntegrityError):
        await InterviewInteractionRepository(db_session).add_delivery(
            interview_session_id=session_a.interview_session.id,
            interviewer_prompt_id=prompt_b.id,
            delivery_attempt=1,
            intended_text="Wrong session prompt.",
            delivery_state="STARTED",
            started_at=datetime.now(UTC),
        )

    await db_session.rollback()
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    prompt_a = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=session_a.interview_session.id,
        origin="SYSTEM",
        kind="TIME_WARNING",
        intent="Session A prompt.",
        status="AUTHORIZED",
    )
    _, transcript_b = await add_transcript_segment(
        db_session,
        session_b,
        server_sequence=1,
        text="Wrong session transcript.",
        speaker="COUNTERQ",
    )

    with pytest.raises(IntegrityError):
        await InterviewInteractionRepository(db_session).add_delivery(
            interview_session_id=session_a.interview_session.id,
            interviewer_prompt_id=prompt_a.id,
            delivery_attempt=1,
            intended_text="Wrong session transcript.",
            actual_transcript_segment_id=transcript_b.id,
            delivery_state="DELIVERED",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )


async def test_candidate_response_optional_prompt_and_multiple_sources(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    interactions = InterviewInteractionRepository(db_session)
    prompt = await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="BASE_QUESTION",
        intent="Ask the candidate to explain their approach.",
        status="AUTHORIZED",
    )
    speech_event, _ = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=1,
        text="I would use a sliding window.",
    )
    code_event, _ = await add_snapshot(db_session, graph, server_sequence=2, version_number=1)
    response = await interactions.add_response(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC) + timedelta(seconds=3),
        completion_reason="COMPLETE",
        summary="Candidate responded with speech and code.",
    )
    spontaneous = await interactions.add_response(
        interview_session_id=graph.interview_session.id,
        started_at=datetime.now(UTC),
        completion_reason="SPONTANEOUS",
    )
    first_source = await interactions.add_response_source(
        interview_session_id=graph.interview_session.id,
        candidate_response_id=response.id,
        interview_event_id=speech_event.id,
        source_role="PRIMARY",
        sequence=1,
    )
    second_source = await interactions.add_response_source(
        interview_session_id=graph.interview_session.id,
        candidate_response_id=response.id,
        interview_event_id=code_event.id,
        source_role="CODE_CONTEXT",
        sequence=2,
    )

    assert response.interviewer_prompt_id == prompt.id
    assert spontaneous.interviewer_prompt_id is None
    assert first_source.sequence == 1
    assert second_source.sequence == 2


async def test_candidate_response_source_rejects_cross_session_event(
    db_session: AsyncSession,
) -> None:
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    event_b, _ = await add_transcript_segment(
        db_session,
        session_b,
        server_sequence=1,
        text="Session B response.",
    )
    response_a = await InterviewInteractionRepository(db_session).add_response(
        interview_session_id=session_a.interview_session.id,
        started_at=datetime.now(UTC),
        completion_reason="SPONTANEOUS",
    )

    with pytest.raises(IntegrityError):
        await InterviewInteractionRepository(db_session).add_response_source(
            interview_session_id=session_a.interview_session.id,
            candidate_response_id=response_a.id,
            interview_event_id=event_b.id,
            source_role="PRIMARY",
            sequence=1,
        )


async def test_full_transcript_driven_causal_chain_is_reconstructable(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai_claim = await create_ai_context(db_session, graph, purpose="CLAIM_EXTRACTION")
    ai_decision = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")
    speech_event, speech_segment = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=1,
        text="I'll use unordered_map because lookup is always O(1).",
    )
    examiner = ExaminerRepository(db_session)
    claim = await examiner.add_candidate_claim(
        interview_session_id=graph.interview_session.id,
        origin_kind="TRANSCRIPT",
        source_transcript_segment_id=speech_segment.id,
        source_event_id=speech_event.id,
        verbatim_excerpt="lookup is always O(1)",
        normalized_claim="unordered_map lookup has guaranteed O(1) time complexity",
        claim_type="COMPLEXITY",
        extraction_confidence=Decimal("0.9400"),
        status="ACCEPTED_AS_INTERPRETATION",
        ai_invocation_id=ai_claim.invocation.id,
        ai_policy_version_id=ai_claim.policy.id,
    )
    decision = await examiner.add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="PROBE",
        target_claim_id=claim.id,
        proposed_probe_strategy="ASSUMPTION_CHALLENGE",
        technical_rationale="Challenge the guaranteed complexity assumption.",
        source_event_watermark=speech_event.server_sequence,
        source_state_version=graph.interview_session.state_version,
        policy_gate_outcome="AUTHORIZED",
        status="AUTHORIZED",
        ai_invocation_id=ai_decision.invocation.id,
        ai_policy_version_id=ai_decision.policy.id,
    )
    interactions = InterviewInteractionRepository(db_session)
    prompt = await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        examiner_decision_id=decision.id,
        origin="EXAMINER_DECISION",
        kind="PROBE",
        probe_strategy="ASSUMPTION_CHALLENGE",
        target_claim_id=claim.id,
        intent="Ask whether unordered_map lookup is actually guaranteed.",
        status="AUTHORIZED",
        authorized_at=datetime.now(UTC),
    )
    delivered_event, delivered_segment = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=2,
        text="You said always. Is that actually guaranteed?",
        speaker="COUNTERQ",
    )
    delivery = await interactions.add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="You said always. Is that actually guaranteed?",
        actual_transcript_segment_id=delivered_segment.id,
        delivery_state="DELIVERED",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC) + timedelta(seconds=2),
    )
    response_event, _ = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=3,
        text="Worst case can degrade with collisions.",
    )
    response = await interactions.add_response(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC) + timedelta(seconds=3),
        completion_reason="COMPLETE",
    )
    await interactions.add_response_source(
        interview_session_id=graph.interview_session.id,
        candidate_response_id=response.id,
        interview_event_id=response_event.id,
        source_role="PRIMARY",
        sequence=1,
    )

    row = await db_session.execute(
        select(
            TranscriptSegment.text,
            CandidateClaim.normalized_claim,
            ExaminerDecision.proposed_probe_strategy,
            InterviewerPrompt.intent,
            InterviewerPromptDelivery.actual_transcript_segment_id,
            CandidateResponse.id,
        )
        .join(CandidateClaim, CandidateClaim.source_transcript_segment_id == TranscriptSegment.id)
        .join(ExaminerDecision, ExaminerDecision.target_claim_id == CandidateClaim.id)
        .join(InterviewerPrompt, InterviewerPrompt.examiner_decision_id == ExaminerDecision.id)
        .join(
            InterviewerPromptDelivery,
            InterviewerPromptDelivery.interviewer_prompt_id == InterviewerPrompt.id,
        )
        .join(CandidateResponse, CandidateResponse.interviewer_prompt_id == InterviewerPrompt.id)
        .where(TranscriptSegment.id == speech_segment.id),
    )
    reconstructed = row.one()

    assert reconstructed.normalized_claim == claim.normalized_claim
    assert reconstructed.proposed_probe_strategy == "ASSUMPTION_CHALLENGE"
    assert reconstructed.actual_transcript_segment_id == delivered_segment.id
    assert reconstructed.id == response.id
    assert delivered_event.server_sequence == 2
    assert delivery.delivery_state == "DELIVERED"


async def test_full_code_driven_chain_does_not_require_spoken_claim(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai_decision = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")
    code_event, snapshot = await add_snapshot(
        db_session, graph, server_sequence=1, version_number=12
    )
    decision = await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="PROBE",
        target_code_snapshot_id=snapshot.id,
        proposed_probe_strategy="EDGE_CASE",
        technical_rationale="Probe the code behavior without a spoken claim.",
        source_event_watermark=code_event.server_sequence,
        source_state_version=graph.interview_session.state_version,
        status="AUTHORIZED",
        ai_invocation_id=ai_decision.invocation.id,
        ai_policy_version_id=ai_decision.policy.id,
    )
    interactions = InterviewInteractionRepository(db_session)
    prompt = await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        examiner_decision_id=decision.id,
        origin="EXAMINER_DECISION",
        kind="PROBE",
        probe_strategy="EDGE_CASE",
        intent="Ask about a code edge case.",
        status="AUTHORIZED",
    )
    _, delivered_segment = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=2,
        text="What happens on an empty input?",
        speaker="COUNTERQ",
    )
    await interactions.add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="What happens on an empty input?",
        actual_transcript_segment_id=delivered_segment.id,
        delivery_state="DELIVERED",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    response_event, _ = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=3,
        text="I need to handle that separately.",
    )
    response = await interactions.add_response(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        started_at=datetime.now(UTC),
        completion_reason="COMPLETE",
    )
    await interactions.add_response_source(
        interview_session_id=graph.interview_session.id,
        candidate_response_id=response.id,
        interview_event_id=response_event.id,
        source_role="PRIMARY",
        sequence=1,
    )

    claim_count = await db_session.scalar(select(func.count()).select_from(CandidateClaim))

    assert claim_count == 0
    assert decision.target_code_snapshot_id == snapshot.id
    assert response.interviewer_prompt_id == prompt.id


async def test_suppressed_stale_decision_path_has_no_fake_delivery(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")
    _, snapshot = await add_snapshot(db_session, graph, server_sequence=1, version_number=1)
    decision = await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="PROBE",
        target_code_snapshot_id=snapshot.id,
        proposed_probe_strategy="FAILURE_MODE",
        technical_rationale="The candidate fixed the issue before prompt delivery.",
        source_event_watermark=1,
        source_state_version=graph.interview_session.state_version,
        policy_gate_outcome="STALE",
        policy_gate_reason="Code changed and target issue no longer exists.",
        status="STALE",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )

    prompts = await db_session.scalar(
        select(func.count())
        .select_from(InterviewerPrompt)
        .where(InterviewerPrompt.examiner_decision_id == decision.id),
    )
    deliveries = await db_session.scalar(
        select(func.count()).select_from(InterviewerPromptDelivery)
    )

    assert prompts == 0
    assert deliveries == 0


async def test_stage1_1b_deletion_behavior_preserves_optional_grouping(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    interactions = InterviewInteractionRepository(db_session)
    prompt = await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="BASE_QUESTION",
        intent="Explain the approach.",
        status="AUTHORIZED",
    )
    response = await interactions.add_response(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        started_at=datetime.now(UTC),
        completion_reason="COMPLETE",
    )
    await interactions.add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="Explain the approach.",
        delivery_state="STARTED",
        started_at=datetime.now(UTC),
    )

    await db_session.execute(delete(InterviewerPrompt).where(InterviewerPrompt.id == prompt.id))
    await db_session.flush()
    await db_session.refresh(response)

    delivery_count = await db_session.scalar(
        select(func.count()).select_from(InterviewerPromptDelivery)
    )

    assert response.interviewer_prompt_id is None
    assert delivery_count == 0


async def test_invalid_constrained_text_values_are_rejected(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")

    with pytest.raises(IntegrityError):
        await ExaminerRepository(db_session).add_examiner_decision(
            interview_session_id=graph.interview_session.id,
            action="TEACH",
            technical_rationale="Invalid action.",
            source_event_watermark=0,
            source_state_version=0,
            status="PROPOSED",
            ai_invocation_id=ai.invocation.id,
            ai_policy_version_id=ai.policy.id,
        )

    await db_session.rollback()
    graph = await create_stage1_graph(db_session)

    with pytest.raises(IntegrityError):
        await InterviewInteractionRepository(db_session).add_prompt(
            interview_session_id=graph.interview_session.id,
            origin="SYSTEM",
            kind="PROBE",
            intent="Probe without strategy.",
            status="AUTHORIZED",
        )

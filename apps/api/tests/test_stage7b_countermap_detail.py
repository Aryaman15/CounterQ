from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.config.settings import Settings
from app.countermap.detail import (
    CounterMapNodeNotFound,
    assemble_candidate_detail,
    attach_development_source,
)
from app.countermap.development_fixtures import (
    DevelopmentCounterMapFixture,
    development_source_code,
    load_development_countermap_fixtures,
)
from app.countermap.models import CounterMapProjection
from app.countermap.projector import CounterMapProjector
from app.countermap.repository import CounterMapProjectionRepository
from app.countermap.routes import countermap_node_detail, development_countermap_node_detail
from app.countermap.schema import (
    CanonicalSourceReference,
    CounterMapDisplayMetadata,
    CounterMapGraph,
    CounterMapNode,
    CounterMapSummary,
    stable_node_id,
)
from app.countermap.source import CounterMapSourceBuilder
from app.evidence.models import (
    Assessment,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
    SkillDimension,
)
from app.execution.models import TestResult as ExecutionTestResult
from app.execution.repository import ExecutionRepository
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.runtime import AcceptEventCommand, InterviewRuntime
from app.observation.models import CodeDiff, CodeSnapshot, InterviewEvent
from app.observation.repository import ObservationRepository
from app.problems.models import Concept


def _fixture(fixture_id: str) -> DevelopmentCounterMapFixture:
    return next(
        item for item in load_development_countermap_fixtures() if item.fixture_id == fixture_id
    )


def _graph(fixture_id: str) -> tuple[DevelopmentCounterMapFixture, CounterMapGraph]:
    fixture = _fixture(fixture_id)
    return fixture, CounterMapProjector().project(fixture.bundle)


async def test_development_detail_uses_the_production_projected_graph() -> None:
    fixture, graph = _graph("simulation-success-and-misconception")
    question = next(item for item in graph.nodes if item.node_type == "QUESTION")

    response = await development_countermap_node_detail(
        fixture.fixture_id,
        question.node_id,
        Settings(app_env="test"),
    )

    assert response.node_id == question.node_id
    assert response.delivered_prompt is not None
    assert response.delivered_prompt.text == question.summary
    assert response.delivered_prompt.why == question.display_metadata.why


async def test_interrupted_detail_never_serializes_intended_suffix_or_private_rationale() -> None:
    fixture, graph = _graph("delivery-and-self-correction-integrity")
    question = next(item for item in graph.nodes if item.node_type == "QUESTION")

    response = await development_countermap_node_detail(
        fixture.fixture_id,
        question.node_id,
        Settings(app_env="test"),
    )
    serialized = response.model_dump_json()

    assert response.delivered_prompt is not None
    assert response.delivered_prompt.text == "What invariant"
    assert response.delivered_prompt.delivery_state == "INTERRUPTED"
    assert "moves backward" not in serialized
    assert "intended_text" not in serialized
    assert "technical_rationale" not in serialized
    assert "ExaminerDecision" not in serialized


async def test_exact_historical_snapshot_v2_wins_when_v5_exists() -> None:
    fixture, graph = _graph("delivery-and-self-correction-integrity")
    assert {item.version for item in fixture.bundle.code_snapshots} == {1, 2, 5}
    code = next(
        item
        for item in graph.nodes
        if item.node_type == "CODE" and item.display_metadata.code_version == 2
    )

    response = await development_countermap_node_detail(
        fixture.fixture_id,
        code.node_id,
        Settings(app_env="test"),
    )

    assert response.source_status == "AVAILABLE"
    assert response.code is not None
    assert response.code.version == 2
    assert "left = max(left, last[char] + 1)" in response.code.source_code
    assert "last.get(char, -1)" not in response.code.source_code


def test_code_hash_or_version_mismatch_fails_closed() -> None:
    fixture, graph = _graph("delivery-and-self-correction-integrity")
    code = next(item for item in graph.nodes if item.node_type == "CODE")
    mismatched = code.model_copy(
        update={
            "display_metadata": code.display_metadata.model_copy(
                update={"content_hash": "sha256:" + "0" * 64}
            )
        }
    )
    detail = assemble_candidate_detail(node=mismatched, bundle=fixture.bundle)

    response = attach_development_source(
        detail=detail,
        node=mismatched,
        bundle=fixture.bundle,
        source_code_for_version=lambda version: development_source_code(
            fixture.fixture_id, version
        ),
    )

    assert response.source_status == "UNAVAILABLE"
    assert response.code is None
    assert response.message is not None
    assert "no code is shown" in response.message


def test_cross_session_source_identity_is_rejected() -> None:
    fixture, graph = _graph("simulation-success-and-misconception")
    node = graph.nodes[0]
    foreign_session = UUID("7b000000-0000-4000-8000-000000000099")
    foreign = node.model_copy(
        update={
            "canonical_sources": [
                item.model_copy(update={"interview_session_id": foreign_session})
                for item in node.canonical_sources
            ]
        }
    )

    with pytest.raises(CounterMapNodeNotFound):
        assemble_candidate_detail(node=foreign, bundle=fixture.bundle)


async def test_test_detail_contains_no_hidden_test_material() -> None:
    fixture, graph = _graph("simulation-success-and-misconception")
    test_node = next(item for item in graph.nodes if item.node_type == "TEST")

    response = await development_countermap_node_detail(
        fixture.fixture_id,
        test_node.node_id,
        Settings(app_env="test"),
    )
    serialized = response.model_dump_json()

    assert response.execution is not None
    assert response.execution.visible_passed == 3
    assert response.execution.visible_failed == 0
    assert "hidden" not in serialized.lower()


async def test_coach_detail_preserves_assistance_and_open_verification_truth() -> None:
    fixture, graph = _graph("coach-assisted-improvement-open-breakpoint")
    evidence = next(
        item
        for item in graph.nodes
        if item.node_type == "EVIDENCE" and item.display_metadata.polarity == "POSITIVE"
    )
    breakpoint = next(item for item in graph.nodes if item.node_type == "BREAKPOINT")

    evidence_detail = await development_countermap_node_detail(
        fixture.fixture_id,
        evidence.node_id,
        Settings(app_env="test"),
    )
    breakpoint_detail = await development_countermap_node_detail(
        fixture.fixture_id,
        breakpoint.node_id,
        Settings(app_env="test"),
    )

    assert evidence_detail.evidence is not None
    assert evidence_detail.evidence.independence_level == "AFTER_LIGHT_GUIDANCE"
    assert breakpoint_detail.breakpoint is not None
    assert breakpoint_detail.breakpoint.status == "OPEN"
    assert breakpoint_detail.breakpoint.independent_verification_required is True


@dataclass(frozen=True)
class PersistedCounterMapDetailFixture:
    session_id: UUID
    projection: CounterMapProjection
    graph: CounterMapGraph
    snapshots: tuple[CodeSnapshot, CodeSnapshot, CodeSnapshot]
    snapshot_events: tuple[InterviewEvent, InterviewEvent, InterviewEvent]
    code_node: CounterMapNode
    question_node: CounterMapNode
    interrupted_question_node: CounterMapNode
    test_node: CounterMapNode
    evidence_node: CounterMapNode


async def _accepted_event(
    session: AsyncSession,
    *,
    session_id: UUID,
    event_type: str,
    source: str,
    key: str,
) -> InterviewEvent:
    result = await InterviewRuntime(session).accept_event(
        AcceptEventCommand(
            session_id=session_id,
            event_type=event_type,
            source=source,
            occurred_at=datetime.now(UTC),
            idempotency_key=key,
            payload={},
        )
    )
    return result.event


async def _persist_prompt_delivery(
    session: AsyncSession,
    *,
    session_id: UUID,
    target_event: InterviewEvent,
    snapshot: CodeSnapshot,
    actual_text: str,
    intended_text: str,
    delivery_state: str,
    key: str,
) -> tuple[UUID, InterviewEvent]:
    interactions = InterviewInteractionRepository(session)
    prompt = await interactions.add_prompt(
        interview_session_id=session_id,
        origin="SYSTEM",
        kind="PROBE",
        probe_strategy="PROVE",
        target_event_id=target_event.id,
        source_code_snapshot_id=snapshot.id,
        intent=intended_text,
        status="INTERRUPTED" if delivery_state == "INTERRUPTED" else "DELIVERED",
        authorized_at=datetime.now(UTC),
    )
    delivered_event = await _accepted_event(
        session,
        session_id=session_id,
        event_type="COUNTERQ_UTTERANCE_DELIVERED",
        source="COUNTERQ_VOICE",
        key=key,
    )
    segment = await ObservationRepository(session).add_transcript_segment(
        session_id=session_id,
        event_id=delivered_event.id,
        speaker="COUNTERQ",
        sequence=delivered_event.server_sequence,
        started_at=datetime.now(UTC),
        text=actual_text,
        interview_stage="IMPLEMENTATION",
        interview_state_version=0,
        delivery_state=delivery_state,
        interrupted_at=datetime.now(UTC) if delivery_state == "INTERRUPTED" else None,
    )
    delivery = await interactions.add_delivery(
        interview_session_id=session_id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text=intended_text,
        delivery_state=delivery_state,
        started_at=datetime.now(UTC),
        actual_transcript_segment_id=segment.id,
        completed_at=datetime.now(UTC) if delivery_state == "DELIVERED" else None,
        interrupted_at=datetime.now(UTC) if delivery_state == "INTERRUPTED" else None,
    )
    return delivery.id, delivered_event


async def _persisted_countermap_detail_fixture(
    session: AsyncSession,
) -> PersistedCounterMapDetailFixture:
    development = await create_development_interview(
        session,
        initial_stage="IMPLEMENTATION",
        language="python",
        mode="COACH",
    )
    session_id = development.interview_session.id
    observation = ObservationRepository(session)
    sources = (
        "left = last[char] + 1\n",
        "left = max(left, last[char] + 1)\n",
        "left = max(left, last.get(char, -1) + 1)\n",
    )
    versions = (1, 2, 5)
    snapshot_events: list[InterviewEvent] = []
    snapshots: list[CodeSnapshot] = []
    for index, (version, source_code) in enumerate(zip(versions, sources, strict=True)):
        event = await _accepted_event(
            session,
            session_id=session_id,
            event_type="CODE_SNAPSHOT_CREATED",
            source="NATIVE_EDITOR",
            key=f"stage7b-db-snapshot-{version}-{session_id}",
        )
        snapshot = await observation.add_code_snapshot(
            session_id=session_id,
            version_number=version,
            parent_snapshot_id=snapshots[index - 1].id if index else None,
            language="python",
            source_code=source_code,
            content_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
            created_from_event_id=event.id,
        )
        snapshot_events.append(event)
        snapshots.append(snapshot)
    await observation.add_code_diff(
        session_id=session_id,
        from_snapshot_id=snapshots[0].id,
        to_snapshot_id=snapshots[1].id,
        diff_format="UNIFIED",
        diff_content="-left = last[char] + 1\n+left = max(left, last[char] + 1)",
        created_from_event_id=snapshot_events[1].id,
        change_summary="Keep the window boundary monotonic.",
        significance="MEANINGFUL",
    )
    await observation.add_code_diff(
        session_id=session_id,
        from_snapshot_id=snapshots[1].id,
        to_snapshot_id=snapshots[2].id,
        diff_format="UNIFIED",
        diff_content="-last[char]\n+last.get(char, -1)",
        created_from_event_id=snapshot_events[2].id,
        change_summary="Use a default lookup.",
        significance="MEANINGFUL",
    )

    delivery_id, delivered_event = await _persist_prompt_delivery(
        session,
        session_id=session_id,
        target_event=snapshot_events[1],
        snapshot=snapshots[1],
        actual_text="Why must the left boundary remain monotonic?",
        intended_text="Why must the left boundary remain monotonic?",
        delivery_state="DELIVERED",
        key=f"stage7b-db-delivered-{session_id}",
    )
    interrupted_delivery_id, interrupted_event = await _persist_prompt_delivery(
        session,
        session_id=session_id,
        target_event=snapshot_events[1],
        snapshot=snapshots[1],
        actual_text="What invariant",
        intended_text="What invariant proves the window never moves backward?",
        delivery_state="INTERRUPTED",
        key=f"stage7b-db-interrupted-{session_id}",
    )

    run_event = await _accepted_event(
        session,
        session_id=session_id,
        event_type="RUN_CLICKED",
        source="NATIVE_RUNNER",
        key=f"stage7b-db-run-{session_id}",
    )
    execution = await ExecutionRepository(session).add_run(
        session_id=session_id,
        run_event_id=run_event.id,
        code_snapshot_id=snapshots[1].id,
        problem_version_id=development.problem_version.id,
        language="python",
        started_at=datetime.now(UTC),
        execution_provider="stage7b-test-double",
        idempotency_key=f"stage7b-db-execution-{session_id}",
    )
    execution.status = "SUCCEEDED"
    execution.completed_at = datetime.now(UTC)
    await ExecutionRepository(session).add_result(
        run_id=execution.id,
        identifier="visible-example",
        input_json={"text": "abba"},
        expected_output="2",
        actual_output="2",
        status="PASSED",
        duration_ms=4,
        failure_classification=None,
    )
    session.add(
        ExecutionTestResult(
            execution_run_id=execution.id,
            test_identifier="private-boundary-case",
            is_visible=False,
            input_json={"secret": "candidate-must-not-see"},
            expected_output="classified-output",
            actual_output="classified-output",
            status="PASSED",
            duration_ms=5,
        )
    )

    concept = Concept(
        canonical_key=f"stage7b_window_invariant_{uuid4().hex}",
        display_name="Window invariant",
        category="ALGORITHMS",
        status="ACTIVE",
        description="Exact Stage 7B integration-test concept.",
    )
    session.add(concept)
    skill = await session.scalar(
        select(SkillDimension).where(SkillDimension.canonical_key == "correctness")
    )
    if skill is None:
        raise AssertionError("The canonical correctness SkillDimension must be seeded")
    policy = AIPolicyVersion(
        policy_key=f"stage7b-detail-{uuid4().hex}",
        version="v1",
        prompt_hash="sha256:" + "1" * 64,
        configuration_json={},
        activated_at=datetime.now(UTC),
    )
    session.add(policy)
    await session.flush()
    invocation = AIInvocation(
        user_id=development.user.id,
        interview_session_id=session_id,
        provider="stage7b-test-double",
        model="deterministic",
        capability="CHEAP_ANALYSIS",
        purpose="SESSION_EVALUATION",
        ai_policy_version_id=policy.id,
        status="SUCCEEDED",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(invocation)
    await session.flush()
    assessment = Assessment(
        interview_session_id=session_id,
        source_code_snapshot_id=snapshots[1].id,
        assessment_dimension="CORRECTNESS",
        polarity="POSITIVE",
        rationale="The correction preserved the window invariant after Coach guidance.",
        confidence=Decimal("0.95"),
        status="VALIDATED",
        ai_invocation_id=invocation.id,
        ai_policy_version_id=policy.id,
    )
    session.add(assessment)
    await session.flush()
    evidence = Evidence(
        interview_session_id=session_id,
        evidence_type="CORRECTNESS",
        polarity="POSITIVE",
        strength="MODERATE",
        confidence=Decimal("0.95"),
        finding="The invariant was restored after light guidance.",
        independence_level="AFTER_LIGHT_GUIDANCE",
        validation_status="VALID",
        originating_assessment_id=assessment.id,
        validation_policy_version_id=policy.id,
    )
    session.add(evidence)
    await session.flush()
    session.add_all(
        [
            EvidenceSource(
                evidence_id=evidence.id,
                interview_event_id=run_event.id,
                interview_session_id=session_id,
                source_role="PRIMARY",
            ),
            EvidenceConcept(
                evidence_id=evidence.id,
                concept_id=concept.id,
                relevance=Decimal("1"),
                is_primary=True,
            ),
            EvidenceSkill(
                evidence_id=evidence.id,
                skill_dimension_id=skill.id,
                relevance=Decimal("1"),
                is_primary=True,
            ),
        ]
    )
    await session.flush()
    await InterviewCompletionService(session).complete(
        session_id=session_id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key=f"stage7b-db-complete-{session_id}",
    )

    bundle = await CounterMapSourceBuilder(session).build(session_id)
    code_node = CounterMapNode(
        node_id=stable_node_id("CODE", snapshots[1].id),
        node_type="CODE",
        subtype="CORRECTION",
        canonical_sources=[
            CanonicalSourceReference(
                source_type="CODE_SNAPSHOT",
                source_id=snapshots[1].id,
                interview_session_id=session_id,
                server_sequence=snapshot_events[1].server_sequence,
                version=2,
                content_hash=snapshots[1].content_hash,
            )
        ],
        title="Corrected code",
        summary="Python code snapshot v2.",
        causal_rank=0,
        stage="IMPLEMENTATION",
        display_metadata=CounterMapDisplayMetadata(
            code_snapshot_id=snapshots[1].id,
            code_version=2,
            content_hash=snapshots[1].content_hash,
            language="python",
        ),
    )
    question_node = CounterMapNode(
        node_id=stable_node_id("QUESTION", delivery_id),
        node_type="QUESTION",
        subtype="PROBE",
        canonical_sources=[
            CanonicalSourceReference(
                source_type="DELIVERED_PROMPT",
                source_id=delivery_id,
                interview_session_id=session_id,
                server_sequence=delivered_event.server_sequence,
            )
        ],
        title="CounterQ asked",
        summary="Why must the left boundary remain monotonic?",
        causal_rank=1,
        stage="IMPLEMENTATION",
        display_metadata=CounterMapDisplayMetadata(delivery_state="DELIVERED"),
    )
    interrupted_question_node = CounterMapNode(
        node_id=stable_node_id("QUESTION", interrupted_delivery_id),
        node_type="QUESTION",
        subtype="PROBE",
        canonical_sources=[
            CanonicalSourceReference(
                source_type="DELIVERED_PROMPT",
                source_id=interrupted_delivery_id,
                interview_session_id=session_id,
                server_sequence=interrupted_event.server_sequence,
            )
        ],
        title="CounterQ asked",
        summary="What invariant",
        causal_rank=1,
        stage="IMPLEMENTATION",
        display_metadata=CounterMapDisplayMetadata(delivery_state="INTERRUPTED"),
    )
    test_node = CounterMapNode(
        node_id=stable_node_id("TEST", execution.id),
        node_type="TEST",
        subtype="VISIBLE_RUN",
        canonical_sources=[
            CanonicalSourceReference(
                source_type="EXECUTION",
                source_id=execution.id,
                interview_session_id=session_id,
                server_sequence=run_event.server_sequence,
            )
        ],
        title="You tested it",
        summary="One visible test passed.",
        causal_rank=2,
        stage="IMPLEMENTATION",
        display_metadata=CounterMapDisplayMetadata(
            execution_status="SUCCEEDED",
            language="python",
            visible_passed=1,
            visible_failed=0,
        ),
    )
    evidence_node = CounterMapNode(
        node_id=stable_node_id("EVIDENCE", evidence.id),
        node_type="EVIDENCE",
        subtype="POSITIVE",
        canonical_sources=[
            CanonicalSourceReference(
                source_type="EVIDENCE",
                source_id=evidence.id,
                interview_session_id=session_id,
                server_sequence=run_event.server_sequence,
            )
        ],
        title="What this showed",
        summary=evidence.finding,
        causal_rank=3,
        stage="IMPLEMENTATION",
        display_metadata=CounterMapDisplayMetadata(
            polarity="POSITIVE",
            strength="MODERATE",
            independence_level="AFTER_LIGHT_GUIDANCE",
        ),
    )
    nodes = [code_node, question_node, interrupted_question_node, test_node, evidence_node]
    graph = CounterMapGraph(
        schema_version="countermap.graph.v1",
        generation_policy_version="countermap-projector.v3",
        interview_session_id=session_id,
        source_watermark=bundle.source_watermark,
        nodes=nodes,
        edges=[],
        summary=CounterMapSummary(
            title="Persisted detail fixture",
            overview="A production-path persisted CounterMap detail fixture.",
            node_counts={
                "CODE": 1,
                "QUESTION": 2,
                "TEST": 1,
                "EVIDENCE": 1,
            },
            relationship_counts={},
        ),
    )
    repository = CounterMapProjectionRepository(session)
    projection, _created = await repository.prepare_generation(
        session_id=session_id,
        generation_request_key=f"stage7b-db-detail-{session_id}",
        source_watermark=bundle.source_watermark,
        source_identity=bundle.source_identity,
    )
    await repository.mark_ready(
        projection=projection,
        graph_json=graph.model_dump(mode="json"),
        generated_at=datetime.now(UTC),
    )
    return PersistedCounterMapDetailFixture(
        session_id=session_id,
        projection=projection,
        graph=graph,
        snapshots=(snapshots[0], snapshots[1], snapshots[2]),
        snapshot_events=(snapshot_events[0], snapshot_events[1], snapshot_events[2]),
        code_node=code_node,
        question_node=question_node,
        interrupted_question_node=interrupted_question_node,
        test_node=test_node,
        evidence_node=evidence_node,
    )


async def _refresh_projection_identity(
    session: AsyncSession,
    fixture: PersistedCounterMapDetailFixture,
) -> None:
    bundle = await CounterMapSourceBuilder(session).build(fixture.session_id)
    fixture.projection.source_identity = bundle.source_identity
    fixture.projection.source_watermark = bundle.source_watermark
    await session.flush()


async def test_production_route_resolves_persisted_ready_snapshot_v2_and_exact_diff(
    db_session: AsyncSession,
) -> None:
    fixture = await _persisted_countermap_detail_fixture(db_session)

    response = await countermap_node_detail(
        fixture.session_id,
        fixture.code_node.node_id,
        db_session,
    )

    assert response.source_status == "AVAILABLE"
    assert response.code is not None
    assert response.code.snapshot_id == fixture.snapshots[1].id
    assert response.code.version == 2
    assert response.code.source_code == fixture.snapshots[1].source_code
    assert response.code.diff is not None
    assert response.code.diff.from_version == 1
    assert response.code.diff.to_version == 2
    assert "last.get" not in response.model_dump_json()


async def test_unrelated_diff_to_same_target_is_ignored_without_replacing_exact_history(
    db_session: AsyncSession,
) -> None:
    fixture = await _persisted_countermap_detail_fixture(db_session)
    db_session.add(
        CodeDiff(
            interview_session_id=fixture.session_id,
            from_snapshot_id=fixture.snapshots[2].id,
            to_snapshot_id=fixture.snapshots[1].id,
            diff_format="UNIFIED",
            diff_content="unrelated newer diff",
            change_summary="Must not be selected.",
            significance="MEANINGFUL",
            created_from_event_id=fixture.snapshot_events[1].id,
        )
    )
    await db_session.flush()
    await _refresh_projection_identity(db_session, fixture)

    response = await countermap_node_detail(
        fixture.session_id,
        fixture.code_node.node_id,
        db_session,
    )

    assert response.code is not None
    assert response.code.diff is not None
    assert response.code.diff.from_version == 1
    assert "unrelated newer diff" not in response.model_dump_json()


async def test_duplicate_exact_diff_is_ambiguous_but_exact_code_remains_available(
    db_session: AsyncSession,
) -> None:
    fixture = await _persisted_countermap_detail_fixture(db_session)
    db_session.add(
        CodeDiff(
            interview_session_id=fixture.session_id,
            from_snapshot_id=fixture.snapshots[0].id,
            to_snapshot_id=fixture.snapshots[1].id,
            diff_format="UNIFIED",
            diff_content="ambiguous duplicate",
            change_summary="Must fail closed.",
            significance="MEANINGFUL",
            created_from_event_id=fixture.snapshot_events[1].id,
        )
    )
    await db_session.flush()
    await _refresh_projection_identity(db_session, fixture)

    response = await countermap_node_detail(
        fixture.session_id,
        fixture.code_node.node_id,
        db_session,
    )

    assert response.code is not None
    assert response.code.source_code == fixture.snapshots[1].source_code
    assert response.code.diff is None


@pytest.mark.parametrize("mismatch", ["id", "version", "hash"])
async def test_production_code_identity_mismatch_fails_closed(
    db_session: AsyncSession,
    mismatch: str,
) -> None:
    fixture = await _persisted_countermap_detail_fixture(db_session)
    reference = fixture.code_node.canonical_sources[0]
    metadata = fixture.code_node.display_metadata
    if mismatch == "id":
        source_id = uuid4()
        reference = reference.model_copy(update={"source_id": source_id})
        metadata = metadata.model_copy(update={"code_snapshot_id": source_id})
    elif mismatch == "version":
        reference = reference.model_copy(update={"version": 1})
        metadata = metadata.model_copy(update={"code_version": 1})
    else:
        bad_hash = "sha256:" + "0" * 64
        reference = reference.model_copy(update={"content_hash": bad_hash})
        metadata = metadata.model_copy(update={"content_hash": bad_hash})
    bad_node = fixture.code_node.model_copy(
        update={"canonical_sources": [reference], "display_metadata": metadata}
    )
    fixture.projection.graph_json = fixture.graph.model_copy(
        update={
            "nodes": [
                bad_node if node.node_id == bad_node.node_id else node
                for node in fixture.graph.nodes
            ]
        }
    ).model_dump(mode="json")
    await db_session.flush()

    response = await countermap_node_detail(fixture.session_id, bad_node.node_id, db_session)

    assert response.source_status == "UNAVAILABLE"
    assert response.code is None


@pytest.mark.parametrize("mismatch", ["identity", "watermark"])
async def test_production_source_identity_or_watermark_mismatch_returns_no_stale_detail(
    db_session: AsyncSession,
    mismatch: str,
) -> None:
    fixture = await _persisted_countermap_detail_fixture(db_session)
    if mismatch == "identity":
        fixture.projection.source_identity = "sha256:" + "f" * 64
    else:
        fixture.projection.source_watermark += 1
    await db_session.flush()

    response = await countermap_node_detail(
        fixture.session_id,
        fixture.code_node.node_id,
        db_session,
    )

    assert response.source_status == "UNAVAILABLE"
    assert response.code is None
    assert response.message is not None
    assert "revalidated" in response.message


async def test_production_question_uses_actual_delivery_and_interrupted_suffix_stays_private(
    db_session: AsyncSession,
) -> None:
    fixture = await _persisted_countermap_detail_fixture(db_session)

    delivered = await countermap_node_detail(
        fixture.session_id,
        fixture.question_node.node_id,
        db_session,
    )
    interrupted = await countermap_node_detail(
        fixture.session_id,
        fixture.interrupted_question_node.node_id,
        db_session,
    )

    assert delivered.delivered_prompt is not None
    assert delivered.delivered_prompt.text == "Why must the left boundary remain monotonic?"
    assert interrupted.delivered_prompt is not None
    assert interrupted.delivered_prompt.text == "What invariant"
    assert interrupted.delivered_prompt.delivery_state == "INTERRUPTED"
    assert "moves backward" not in interrupted.model_dump_json()


async def test_production_test_detail_serializes_only_candidate_visible_results(
    db_session: AsyncSession,
) -> None:
    fixture = await _persisted_countermap_detail_fixture(db_session)

    response = await countermap_node_detail(
        fixture.session_id,
        fixture.test_node.node_id,
        db_session,
    )
    serialized = response.model_dump_json()

    assert response.execution is not None
    assert [item.test_identifier for item in response.execution.visible_tests] == [
        "visible-example"
    ]
    assert "private-boundary-case" not in serialized
    assert "candidate-must-not-see" not in serialized
    assert "classified-output" not in serialized


async def test_production_coach_evidence_preserves_assisted_independence(
    db_session: AsyncSession,
) -> None:
    fixture = await _persisted_countermap_detail_fixture(db_session)

    response = await countermap_node_detail(
        fixture.session_id,
        fixture.evidence_node.node_id,
        db_session,
    )

    assert response.evidence is not None
    assert response.evidence.independence_level == "AFTER_LIGHT_GUIDANCE"


async def test_production_route_requires_a_current_ready_projection(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(
        db_session,
        initial_stage="IMPLEMENTATION",
    )
    await InterviewCompletionService(db_session).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key=f"stage7b-no-ready-{development.interview_session.id}",
    )

    with pytest.raises(HTTPException) as error:
        await countermap_node_detail(
            development.interview_session.id,
            "cmn_000000000000000000000000",
            db_session,
        )

    assert error.value.status_code == 409


async def test_cross_session_graph_and_malformed_graph_fail_safely(
    db_session: AsyncSession,
) -> None:
    fixture = await _persisted_countermap_detail_fixture(db_session)
    other = await create_development_interview(
        db_session,
        initial_stage="IMPLEMENTATION",
    )
    await InterviewCompletionService(db_session).complete(
        session_id=other.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key=f"stage7b-other-session-{other.interview_session.id}",
    )
    other_bundle = await CounterMapSourceBuilder(db_session).build(other.interview_session.id)
    repository = CounterMapProjectionRepository(db_session)
    other_projection, _created = await repository.prepare_generation(
        session_id=other.interview_session.id,
        generation_request_key=f"stage7b-cross-session-{other.interview_session.id}",
        source_watermark=other_bundle.source_watermark,
        source_identity=other_bundle.source_identity,
    )
    await repository.mark_ready(
        projection=other_projection,
        graph_json=fixture.graph.model_dump(mode="json"),
        generated_at=datetime.now(UTC),
    )

    with pytest.raises(HTTPException) as cross_session:
        await countermap_node_detail(
            other.interview_session.id,
            fixture.code_node.node_id,
            db_session,
        )
    assert cross_session.value.status_code == 404

    other_projection.graph_json = {"schema_version": "countermap.graph.v1"}
    await db_session.flush()
    with pytest.raises(HTTPException) as malformed:
        await countermap_node_detail(
            other.interview_session.id,
            fixture.code_node.node_id,
            db_session,
        )
    assert malformed.value.status_code == 404

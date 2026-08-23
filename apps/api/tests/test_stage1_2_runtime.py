from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_stage1_1a_persistence import create_stage1_graph
from test_stage1_1b_causal_persistence import add_transcript_segment, create_ai_context

from app.auth.models import User
from app.db.session import build_engine
from app.examiner.repository import ExaminerRepository
from app.interviews.budget_policy import budget_availability
from app.interviews.dev_factory import create_development_interview
from app.interviews.floor import ConversationFloor
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import (
    InterviewerPromptDelivery,
    InterviewStageTransition,
)
from app.interviews.prompt_policy import (
    DeliveryStateInvalid,
    PromptNotDeliverable,
    candidate_visible_delivery,
    ensure_no_active_delivery,
    validate_delivery_state,
    validate_examiner_decision_delivery_eligibility,
    validate_prompt_delivery_eligibility,
    validate_prompt_origin,
)
from app.interviews.runtime import (
    AcceptEventCommand,
    ActivePromptDeliveryBlocksTransition,
    IdempotencyConflict,
    InterviewRuntime,
    SessionClosed,
    SessionDeadlineReached,
    StaleStateVersion,
    TransitionCommand,
)
from app.interviews.state_machine import IllegalStageTransition, TransitionContext, can_transition
from app.observation.models import InterviewEvent


def utcnow() -> datetime:
    return datetime.now(UTC)


async def accept_candidate_event(
    db_session: AsyncSession,
    session_id: UUID,
    *,
    key: str | None = None,
    payload: dict[str, object] | None = None,
    expected_state_version: int | None = None,
) -> InterviewEvent:
    accepted = await InterviewRuntime(db_session).accept_event(
        AcceptEventCommand(
            session_id=session_id,
            event_type="CANDIDATE_DECLARED_DONE",
            source="BROWSER_EXTENSION",
            occurred_at=utcnow(),
            idempotency_key=key,
            payload=payload,
            expected_state_version=expected_state_version,
        ),
    )
    return accepted.event


async def test_legal_stage_transition_path_persists_history(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    runtime = InterviewRuntime(db_session)
    path = [
        ("INTRODUCTION", "DEPENDENCIES_READY", TransitionContext("DEPENDENCIES_READY")),
        ("PROBLEM_UNDERSTANDING", "INTRO_COMPLETE", TransitionContext("INTRO_COMPLETE")),
        ("APPROACH_DISCOVERY", "PROBLEM_UNDERSTOOD", TransitionContext("PROBLEM_UNDERSTOOD")),
        ("APPROACH_DEFENSE", "CONCRETE_APPROACH", TransitionContext("CONCRETE_APPROACH")),
        ("IMPLEMENTATION", "APPROACH_SUFFICIENT", TransitionContext("APPROACH_SUFFICIENT")),
        ("TESTING_DEBUGGING", "MEANINGFUL_TESTING", TransitionContext("MEANINGFUL_TESTING")),
        ("COMPLEXITY_EDGE_CASES", "SOLUTION_EXPLORED", TransitionContext("SOLUTION_EXPLORED")),
        ("CONSTRAINT_MUTATION", "MUTATION_USEFUL", TransitionContext("MUTATION_USEFUL")),
        ("FINAL_DEFENSE", "TRANSFER_ASSESSED", TransitionContext("TRANSFER_ASSESSED")),
        ("WRAP_UP", "DEFENSE_COMPLETE", TransitionContext("DEFENSE_COMPLETE", wrap_only=True)),
        ("COMPLETED", "CLOSING_COMPLETE", TransitionContext("CLOSING_COMPLETE")),
    ]

    for next_stage, trigger, context in path:
        previous_version = graph.interview_session.state_version
        transition = await runtime.transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage=next_stage,
                trigger=trigger,
                expected_state_version=previous_version,
                occurred_at=utcnow(),
                context=context,
                idempotency_key=f"transition-{next_stage}",
            ),
        )
        assert transition.state_version == previous_version + 1
        assert transition.to_stage == next_stage

    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewStageTransition)
        .where(InterviewStageTransition.interview_session_id == graph.interview_session.id),
    )

    assert graph.interview_session.current_stage == "COMPLETED"
    assert graph.interview_session.status == "COMPLETED"
    assert graph.interview_session.state_version == 11
    assert graph.interview_session.last_server_sequence == 11
    assert transition_count == 11


async def test_illegal_stage_transitions_are_rejected(db_session: AsyncSession) -> None:
    graph = await create_stage1_graph(db_session)
    runtime = InterviewRuntime(db_session)

    with pytest.raises(IllegalStageTransition):
        await runtime.transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage="IMPLEMENTATION",
                trigger="SKIP_AHEAD",
                expected_state_version=0,
                occurred_at=utcnow(),
            ),
        )

    graph.interview_session.current_stage = "COMPLETED"
    graph.interview_session.status = "COMPLETED"
    await db_session.flush()

    with pytest.raises(SessionClosed):
        await runtime.transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage="APPROACH_DISCOVERY",
                trigger="REOPEN",
                expected_state_version=0,
                occurred_at=utcnow(),
            ),
        )

    assert can_transition(
        "APPROACH_DEFENSE",
        "APPROACH_DISCOVERY",
        TransitionContext("APPROACH_ABANDONED"),
    )


async def test_state_version_rejects_stale_mutations(db_session: AsyncSession) -> None:
    graph = await create_stage1_graph(db_session)
    runtime = InterviewRuntime(db_session)

    await runtime.transition(
        TransitionCommand(
            session_id=graph.interview_session.id,
            to_stage="INTRODUCTION",
            trigger="DEPENDENCIES_READY",
            expected_state_version=0,
            occurred_at=utcnow(),
        ),
    )

    with pytest.raises(StaleStateVersion):
        await runtime.transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage="PROBLEM_UNDERSTANDING",
                trigger="INTRO_COMPLETE",
                expected_state_version=0,
                occurred_at=utcnow(),
            ),
        )


async def test_transition_idempotency_returns_existing_and_rejects_conflict(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    runtime = InterviewRuntime(db_session)
    command = TransitionCommand(
        session_id=graph.interview_session.id,
        to_stage="INTRODUCTION",
        trigger="DEPENDENCIES_READY",
        expected_state_version=0,
        occurred_at=utcnow(),
        idempotency_key="transition-introduction",
    )

    first = await runtime.transition(command)
    retry = await runtime.transition(command)

    assert first.id == retry.id
    assert graph.interview_session.current_stage == "INTRODUCTION"
    assert graph.interview_session.state_version == 1
    assert graph.interview_session.last_server_sequence == 1

    with pytest.raises(IdempotencyConflict):
        await runtime.transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage="IMPLEMENTATION",
                trigger="SKIP_AHEAD",
                expected_state_version=0,
                occurred_at=utcnow(),
                idempotency_key="transition-introduction",
            ),
        )


async def test_server_sequence_allocates_authoritative_values(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)

    first = await accept_candidate_event(db_session, graph.interview_session.id)
    second = await accept_candidate_event(db_session, graph.interview_session.id)
    third = await accept_candidate_event(db_session, graph.interview_session.id)

    assert [first.server_sequence, second.server_sequence, third.server_sequence] == [1, 2, 3]
    assert graph.interview_session.last_server_sequence == 3


async def test_concurrent_event_acceptance_cannot_duplicate_server_sequence() -> None:
    engine = build_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    user_id: UUID | None = None
    try:
        async with maker() as setup_session:
            dev = await create_development_interview(setup_session)
            user_id = dev.user.id
            session_id = dev.interview_session.id
            await setup_session.commit()

        async def accept_with_key(key: str) -> int:
            async with maker() as session:
                async with session.begin():
                    event = await accept_candidate_event(session, session_id, key=key)
                    return event.server_sequence

        sequences = await asyncio.gather(
            accept_with_key("concurrent-a"),
            accept_with_key("concurrent-b"),
        )

        async with maker() as verify_session:
            stored_sequences = await verify_session.scalars(
                select(InterviewEvent.server_sequence)
                .where(InterviewEvent.interview_session_id == session_id)
                .order_by(InterviewEvent.server_sequence),
            )
            assert sorted(sequences) == [1, 2]
            assert list(stored_sequences) == [1, 2]
    finally:
        if user_id is not None:
            async with maker() as cleanup_session:
                async with cleanup_session.begin():
                    await cleanup_session.execute(delete(User).where(User.id == user_id))
        await engine.dispose()


async def test_idempotency_returns_existing_event_and_rejects_conflict(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    runtime = InterviewRuntime(db_session)
    command = AcceptEventCommand(
        session_id=graph.interview_session.id,
        event_type="RUN_CLICKED",
        source="NATIVE_RUNNER",
        occurred_at=utcnow(),
        idempotency_key="run-click-1",
        payload={"button": "run"},
    )

    first = await runtime.accept_event(command)
    second = await runtime.accept_event(command)

    assert first.created is True
    assert second.created is False
    assert first.event.id == second.event.id
    assert graph.interview_session.last_server_sequence == 1

    with pytest.raises(IdempotencyConflict):
        await runtime.accept_event(
            AcceptEventCommand(
                session_id=graph.interview_session.id,
                event_type="RUN_CLICKED",
                source="NATIVE_RUNNER",
                occurred_at=utcnow(),
                idempotency_key="run-click-1",
                payload={"button": "different"},
            ),
        )


async def test_transition_atomicity_rolls_back_session_event_and_history(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)

    def fail_before_flush() -> None:
        raise RuntimeError("forced transition failure")

    runtime = InterviewRuntime(db_session, before_transition_flush=fail_before_flush)
    savepoint = await db_session.begin_nested()
    with pytest.raises(RuntimeError):
        await runtime.transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage="INTRODUCTION",
                trigger="DEPENDENCIES_READY",
                expected_state_version=0,
                occurred_at=utcnow(),
            ),
        )
    await savepoint.rollback()
    await db_session.refresh(graph.interview_session)

    event_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewEvent)
        .where(InterviewEvent.interview_session_id == graph.interview_session.id),
    )
    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewStageTransition)
        .where(InterviewStageTransition.interview_session_id == graph.interview_session.id),
    )

    assert graph.interview_session.current_stage == "SETUP"
    assert graph.interview_session.state_version == 0
    assert graph.interview_session.last_server_sequence == 0
    assert event_count == 0
    assert transition_count == 0


async def test_normal_transition_rejects_active_prompt_delivery(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="BASE_QUESTION",
        intent="Begin the interview.",
        status="AUTHORIZED",
    )
    await InterviewInteractionRepository(db_session).add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="Let's begin.",
        delivery_state="STARTED",
        started_at=utcnow(),
    )

    with pytest.raises(ActivePromptDeliveryBlocksTransition):
        await InterviewRuntime(db_session).transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage="INTRODUCTION",
                trigger="DEPENDENCIES_READY",
                expected_state_version=0,
                occurred_at=utcnow(),
            ),
        )

    await db_session.refresh(graph.interview_session)
    assert graph.interview_session.current_stage == "SETUP"
    assert graph.interview_session.state_version == 0


async def test_exceptional_transition_can_bypass_active_delivery_guard(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    graph.interview_session.current_stage = "IMPLEMENTATION"
    graph.interview_session.state_version = 3
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="TIME_WARNING",
        intent="Move to wrap-up at candidate request.",
        status="AUTHORIZED",
    )
    await InterviewInteractionRepository(db_session).add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="We can wrap up now.",
        delivery_state="STARTED",
        started_at=utcnow(),
    )

    transition = await InterviewRuntime(db_session).transition(
        TransitionCommand(
            session_id=graph.interview_session.id,
            to_stage="WRAP_UP",
            trigger="CANDIDATE_REQUESTED_FINISH",
            expected_state_version=3,
            occurred_at=utcnow(),
            context=TransitionContext(
                "CANDIDATE_REQUESTED_FINISH",
                candidate_requested_finish=True,
            ),
        ),
    )

    assert transition.to_stage == "WRAP_UP"
    assert graph.interview_session.current_stage == "WRAP_UP"
    assert graph.interview_session.state_version == 4


async def test_deadline_and_completion_reject_ordinary_activity(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    original_deadline = graph.interview_session.deadline_at

    def expired_clock() -> datetime:
        return original_deadline + timedelta(seconds=1)

    with pytest.raises(SessionDeadlineReached):
        await InterviewRuntime(db_session, clock=expired_clock).accept_event(
            AcceptEventCommand(
                session_id=graph.interview_session.id,
                event_type="CANDIDATE_DECLARED_DONE",
                source="BROWSER_EXTENSION",
                occurred_at=utcnow(),
            ),
        )

    assert graph.interview_session.deadline_at == original_deadline

    graph = await create_stage1_graph(db_session)
    graph.interview_session.current_stage = "WRAP_UP"
    graph.interview_session.state_version = 5
    await db_session.flush()
    await InterviewRuntime(db_session).complete_interview(
        session_id=graph.interview_session.id,
        expected_state_version=5,
        occurred_at=utcnow(),
    )

    with pytest.raises(SessionClosed):
        await accept_candidate_event(db_session, graph.interview_session.id)


async def test_stage_transition_rejects_cross_session_event_provenance(
    db_session: AsyncSession,
) -> None:
    session_a = await create_stage1_graph(db_session)
    session_b = await create_stage1_graph(db_session)
    event_b = await accept_candidate_event(db_session, session_b.interview_session.id)
    transition = InterviewStageTransition(
        interview_session_id=session_a.interview_session.id,
        from_stage="SETUP",
        to_stage="INTRODUCTION",
        state_version=1,
        trigger="DEPENDENCIES_READY",
        occurred_at=utcnow(),
        event_id=event_b.id,
        transition_policy_version="stage1.2-state-machine.v1",
    )
    db_session.add(transition)

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_prompt_causal_validation_blocks_stale_decision_delivery(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")
    proposed = await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="PROBE",
        proposed_probe_strategy="ASSUMPTION_CHALLENGE",
        technical_rationale="Candidate may have made a useful assumption claim.",
        source_event_watermark=0,
        source_state_version=0,
        status="PROPOSED",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )
    proposed_prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="EXAMINER_DECISION",
        examiner_decision_id=proposed.id,
        kind="PROBE",
        probe_strategy="ASSUMPTION_CHALLENGE",
        intent="Challenge the assumption.",
        status="AUTHORIZED",
    )

    validate_prompt_origin(origin="EXAMINER_DECISION", examiner_decision=proposed)
    with pytest.raises(PromptNotDeliverable):
        validate_examiner_decision_delivery_eligibility(proposed)
    with pytest.raises(PromptNotDeliverable):
        validate_prompt_delivery_eligibility(
            prompt=proposed_prompt,
            examiner_decision=proposed,
        )

    authorized = await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="PROBE",
        proposed_probe_strategy="ASSUMPTION_CHALLENGE",
        technical_rationale="Policy gate authorized a still-useful probe.",
        source_event_watermark=0,
        source_state_version=0,
        status="AUTHORIZED",
        policy_gate_outcome="AUTHORIZED",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )
    authorized_prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="EXAMINER_DECISION",
        examiner_decision_id=authorized.id,
        kind="PROBE",
        probe_strategy="ASSUMPTION_CHALLENGE",
        intent="Challenge the authorized assumption.",
        status="AUTHORIZED",
    )

    validate_prompt_delivery_eligibility(
        prompt=authorized_prompt,
        examiner_decision=authorized,
    )

    stale = await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="PROBE",
        proposed_probe_strategy="ASSUMPTION_CHALLENGE",
        technical_rationale="Candidate already self-corrected.",
        source_event_watermark=0,
        source_state_version=0,
        status="STALE",
        policy_gate_outcome="STALE",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )
    with pytest.raises(PromptNotDeliverable):
        validate_examiner_decision_delivery_eligibility(stale)

    with pytest.raises(IntegrityError):
        await InterviewInteractionRepository(db_session).add_prompt(
            interview_session_id=graph.interview_session.id,
            origin="SYSTEM",
            examiner_decision_id=stale.id,
            kind="TIME_WARNING",
            intent="Invalid fabricated examiner provenance.",
            status="AUTHORIZED",
        )


async def test_delivery_state_and_authorization_visibility_policy(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    _, delivered_segment = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=1,
        speaker="COUNTERQ",
        text="We are almost",
    )
    delivered_segment.delivery_state = "INTERRUPTED"
    delivered_segment.interrupted_at = utcnow() + timedelta(seconds=1)
    interactions = InterviewInteractionRepository(db_session)
    prompt = await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="TIME_WARNING",
        intent="Warn about time.",
        status="AUTHORIZED",
    )
    delivery = await interactions.add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="We are almost out of time.",
        delivery_state="INTERRUPTED",
        started_at=utcnow(),
        actual_transcript_segment_id=delivered_segment.id,
        interrupted_at=utcnow() + timedelta(seconds=1),
    )

    validate_delivery_state(delivery)

    prompt.status = "DELIVERED"
    visible = candidate_visible_delivery(delivery)

    assert visible is not None
    assert visible.actual_transcript_segment_id == delivered_segment.id
    assert visible.is_partial is True
    assert not hasattr(visible, "intended_text")
    assert delivery.delivery_state == "INTERRUPTED"

    no_factual_delivery = InterviewerPromptDelivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=2,
        intended_text="This must not leak from prompt status.",
        delivery_state="INTERRUPTED",
        started_at=utcnow(),
        interrupted_at=utcnow() + timedelta(seconds=1),
    )
    assert candidate_visible_delivery(no_factual_delivery) is None

    invalid = InterviewerPromptDelivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=3,
        intended_text="Invalid delivered state.",
        delivery_state="DELIVERED",
        started_at=utcnow(),
    )
    with pytest.raises(DeliveryStateInvalid):
        validate_delivery_state(invalid)


async def test_only_one_active_prompt_delivery_may_own_floor(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="TIME_WARNING",
        intent="Warn about time.",
        status="AUTHORIZED",
    )
    await InterviewInteractionRepository(db_session).add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="Time warning.",
        delivery_state="STARTED",
        started_at=utcnow(),
    )

    with pytest.raises(PromptNotDeliverable):
        await ensure_no_active_delivery(db_session, graph.interview_session.id)

    with pytest.raises(IntegrityError):
        await InterviewInteractionRepository(db_session).add_delivery(
            interview_session_id=graph.interview_session.id,
            interviewer_prompt_id=prompt.id,
            delivery_attempt=2,
            intended_text="Second simultaneous delivery.",
            delivery_state="STARTED",
            started_at=utcnow(),
        )


async def test_concurrent_delivery_starts_are_backstopped_by_database_index() -> None:
    engine = build_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    user_id: UUID | None = None
    session_id: UUID | None = None
    prompt_id: UUID | None = None
    try:
        async with maker() as setup_session:
            async with setup_session.begin():
                dev = await create_development_interview(setup_session)
                user_id = dev.user.id
                session_id = dev.interview_session.id
                prompt = await InterviewInteractionRepository(setup_session).add_prompt(
                    interview_session_id=session_id,
                    origin="SYSTEM",
                    kind="TIME_WARNING",
                    intent="Warn about time.",
                    status="AUTHORIZED",
                )
                prompt_id = prompt.id

        async def start_delivery(attempt: int) -> UUID:
            assert session_id is not None
            assert prompt_id is not None
            async with maker() as session:
                async with session.begin():
                    delivery = await InterviewInteractionRepository(session).add_delivery(
                        interview_session_id=session_id,
                        interviewer_prompt_id=prompt_id,
                        delivery_attempt=attempt,
                        intended_text=f"Delivery attempt {attempt}.",
                        delivery_state="STARTED",
                        started_at=utcnow(),
                    )
                    return delivery.id

        results = await asyncio.gather(
            start_delivery(1),
            start_delivery(2),
            return_exceptions=True,
        )

        assert sum(isinstance(result, UUID) for result in results) == 1
        assert sum(isinstance(result, IntegrityError) for result in results) == 1

        async with maker() as verify_session:
            assert session_id is not None
            delivery_count = await verify_session.scalar(
                select(func.count())
                .select_from(InterviewerPromptDelivery)
                .where(InterviewerPromptDelivery.interview_session_id == session_id)
                .where(InterviewerPromptDelivery.delivery_state == "STARTED"),
            )
            assert delivery_count == 1
    finally:
        if user_id is not None:
            async with maker() as cleanup_session:
                async with cleanup_session.begin():
                    await cleanup_session.execute(delete(User).where(User.id == user_id))
        await engine.dispose()


async def test_conversation_floor_candidate_speech_wins_and_blocks_overlap() -> None:
    counterq_speaking = ConversationFloor().try_counterq_speaking("delivery-1")

    assert counterq_speaking is not None
    assert counterq_speaking.try_counterq_speaking("delivery-2") is None

    candidate_floor = counterq_speaking.candidate_speech_started()

    assert candidate_floor.state == "CANDIDATE_SPEAKING"
    assert candidate_floor.active_prompt_delivery_id is None
    assert candidate_floor.interrupted_prompt_delivery_id == "delivery-1"
    assert candidate_floor.try_counterq_speaking("delivery-2") is None

    thinking_floor = candidate_floor.candidate_paused()

    assert thinking_floor.state == "CANDIDATE_THINKING"
    assert thinking_floor.try_counterq_speaking("delivery-3") is not None
    assert ConversationFloor(state="CANDIDATE_SPEAKING").try_counterq_speaking("delivery-4") is None


async def test_budget_helpers_do_not_consume_probe_for_decision_creation(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    ai = await create_ai_context(db_session, graph, purpose="LIVE_EXAMINER")
    before = budget_availability(graph.budget)

    await ExaminerRepository(db_session).add_examiner_decision(
        interview_session_id=graph.interview_session.id,
        action="PROBE",
        proposed_probe_strategy="WHY",
        technical_rationale="Storage of a recommendation does not consume a probe.",
        source_event_watermark=0,
        source_state_version=0,
        status="PROPOSED",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )
    after = budget_availability(graph.budget)

    assert before.probe_available is True
    assert after.probe_available is True
    assert graph.budget.probes_used == 0

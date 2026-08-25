"""Focused deterministic acceptance tests for Stage 2A lifecycle authority."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_stage1_1a_persistence import create_stage1_graph

from app.db.session import build_engine
from app.interviews.dev_factory import create_development_interview
from app.interviews.models import InterviewStageTransition
from app.interviews.runtime import (
    ActivePromptDeliveryBlocksTransition,
    InterviewRuntime,
    StaleStateVersion,
    TransitionCommand,
)
from app.interviews.state_machine import IllegalStageTransition, TransitionContext
from app.interviews.template_policy import STANDARD_STAGE_PLAN, template_policy
from app.interviews.time_policy import evaluate_time_policy


def fixed_now() -> datetime:
    return datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def test_template_durations_and_standard_plan_are_configuration() -> None:
    assert template_policy("QUICK_DRILL").configured_duration_seconds == 600
    assert template_policy("SOLUTION_DEFENSE").configured_duration_seconds == 900
    standard = template_policy("STANDARD_CODING_INTERVIEW")
    assert standard.configured_duration_seconds == 1800
    assert sum(entry.target_seconds for entry in STANDARD_STAGE_PLAN) == 1800
    assert standard.protected_final_defense_seconds == 120
    assert standard.protected_wrap_up_seconds == 60
    mutation = next(entry for entry in STANDARD_STAGE_PLAN if entry.stage == "CONSTRAINT_MUTATION")
    assert mutation.skippable


def test_time_policy_protects_final_defense_and_wrap_up() -> None:
    policy = template_policy("STANDARD_CODING_INTERVIEW")
    start = fixed_now()
    defense = evaluate_time_policy(
        policy=policy,
        current_stage="IMPLEMENTATION",
        stage_started_at=start,
        deadline_at=start + timedelta(seconds=180),
        now=start,
    )
    wrap = evaluate_time_policy(
        policy=policy,
        current_stage="IMPLEMENTATION",
        stage_started_at=start,
        deadline_at=start + timedelta(seconds=60),
        now=start,
    )
    mutation = evaluate_time_policy(
        policy=policy,
        current_stage="COMPLEXITY_EDGE_CASES",
        stage_started_at=start,
        deadline_at=start + timedelta(seconds=180),
        now=start,
    )
    assert defense.pressure == "DEFENSE_RESERVED"
    assert defense.optional_probes_suppressed is True
    assert wrap.pressure == "WRAP_ONLY" and wrap.wrap_only is True
    assert mutation.mutation_should_skip is True


async def test_transition_path_is_atomic_versioned_and_server_ordered(
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
    ]
    transitions = []
    for index, (stage, trigger, context) in enumerate(path, start=1):
        transitions.append(
            await runtime.transition(
                TransitionCommand(
                    session_id=graph.interview_session.id,
                    to_stage=stage,
                    trigger=trigger,
                    expected_state_version=index - 1,
                    occurred_at=datetime.now(UTC),
                    context=context,
                    idempotency_key=f"stage2a-path-{index}",
                )
            )
        )
    assert graph.interview_session.state_version == len(path)
    assert graph.interview_session.last_server_sequence == len(path)
    assert [transition.state_version for transition in transitions] == [1, 2, 3, 4, 5]
    assert all(transition.event_id for transition in transitions)


async def test_illegal_and_stale_transitions_do_not_mutate_state(db_session: AsyncSession) -> None:
    graph = await create_stage1_graph(db_session)
    runtime = InterviewRuntime(db_session)
    with pytest.raises(IllegalStageTransition):
        await runtime.transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage="IMPLEMENTATION",
                trigger="SKIP_AHEAD",
                expected_state_version=0,
                occurred_at=datetime.now(UTC),
            )
        )
    assert graph.interview_session.state_version == 0
    await runtime.transition(
        TransitionCommand(
            session_id=graph.interview_session.id,
            to_stage="INTRODUCTION",
            trigger="DEPENDENCIES_READY",
            expected_state_version=0,
            occurred_at=datetime.now(UTC),
        )
    )
    with pytest.raises(StaleStateVersion):
        await runtime.transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage="PROBLEM_UNDERSTANDING",
                trigger="INTRO_COMPLETE",
                expected_state_version=0,
                occurred_at=datetime.now(UTC),
            )
        )
    assert graph.interview_session.state_version == 1


async def test_development_standard_session_has_server_owned_30_minute_deadline(
    db_session: AsyncSession,
) -> None:
    before = datetime.now(UTC)
    development = await create_development_interview(db_session, initial_stage="SETUP")
    assert development.template == "STANDARD_CODING_INTERVIEW"
    assert development.configuration.configured_duration_seconds == 1800
    assert development.budget.max_duration_seconds == 1800
    duration = development.interview_session.deadline_at - development.interview_session.started_at
    assert duration == timedelta(seconds=1800)
    assert development.interview_session.started_at >= before


async def test_concurrent_duplicate_transition_creates_one_history_row() -> None:
    engine = build_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as setup:
            async with setup.begin():
                development = await create_development_interview(setup, initial_stage="SETUP")
        session_id = development.interview_session.id

        async def transition() -> InterviewStageTransition:
            async with maker() as session:
                async with session.begin():
                    return await InterviewRuntime(session).transition(
                        TransitionCommand(
                            session_id=session_id,
                            to_stage="INTRODUCTION",
                            trigger="DEPENDENCIES_READY",
                            expected_state_version=0,
                            occurred_at=datetime.now(UTC),
                            idempotency_key="stage2a-concurrent-introduction",
                        )
                    )

        first, second = await asyncio.gather(transition(), transition())
        assert first.id == second.id
        async with maker() as verify:
            count = await verify.scalar(
                select(func.count())
                .select_from(InterviewStageTransition)
                .where(InterviewStageTransition.interview_session_id == session_id)
            )
        assert count == 1
    finally:
        await engine.dispose()


async def test_normal_transition_is_blocked_by_active_delivery(db_session: AsyncSession) -> None:
    graph = await create_stage1_graph(db_session)
    graph.interview_session.current_stage = "IMPLEMENTATION"
    from app.interviews.interaction_repository import InterviewInteractionRepository

    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="TIME_WARNING",
        intent="Finish this thought.",
        status="AUTHORIZED",
    )
    await InterviewInteractionRepository(db_session).add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="Finish this thought.",
        delivery_state="STARTED",
        started_at=datetime.now(UTC),
    )
    with pytest.raises(ActivePromptDeliveryBlocksTransition):
        await InterviewRuntime(db_session).transition(
            TransitionCommand(
                session_id=graph.interview_session.id,
                to_stage="TESTING_DEBUGGING",
                trigger="MEANINGFUL_TESTING",
                expected_state_version=0,
                occurred_at=datetime.now(UTC),
            )
        )

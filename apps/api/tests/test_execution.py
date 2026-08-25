# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import TestResult as ExecutionTestResult
from app.execution.provider import ExecutionCaseOutcome, ExecutionOutcome, FakeExecutorProvider
from app.execution.service import ExecutionService, RunCommand
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.runtime import SessionClosed
from app.observation.models import InterviewEvent


async def test_run_uses_exact_canonical_snapshot_and_persists_visible_results(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    outcome = ExecutionOutcome(
        status="SUCCEEDED",
        provider_run_id="sandbox-1",
        cases=(
            ExecutionCaseOutcome("visible-1", "3", "PASSED", 4),
            ExecutionCaseOutcome("visible-2", "1", "PASSED", 4),
            ExecutionCaseOutcome("visible-3", "3", "PASSED", 4),
        ),
    )
    provider = FakeExecutorProvider(outcome)
    service = ExecutionService(db_session, provider)
    command = RunCommand(
        session_id=development.interview_session.id,
        source_code="class Solution { public: int lengthOfLongestSubstring(string s) { return 3; } };",
        idempotency_key="run-1",
        client_event_id="run-client-1",
        client_instance_id="browser-1",
        client_sequence=1,
    )
    run, request, created = await service.begin(command)
    assert created and request.source_code == command.source_code
    completed = await service.complete(run.id, await service.execute(request))
    retry, _, retry_created = await service.begin(command)
    assert completed.status == "SUCCEEDED"
    assert retry.id == run.id and not retry_created
    assert len(provider.requests) == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(ExecutionTestResult)
        .where(ExecutionTestResult.execution_run_id == run.id)
    ) == 3
    events = list(
        (await db_session.scalars(select(InterviewEvent).order_by(InterviewEvent.server_sequence))).all()
    )
    session_events = [
        event for event in events if event.interview_session_id == development.interview_session.id
    ]
    assert [event.event_type for event in session_events] == [
        "CODE_SNAPSHOT_CREATED",
        "RUN_CLICKED",
        "COMPILE_COMPLETED",
        "TEST_COMPLETED",
    ]
    assert session_events[1].code_snapshot_id == run.code_snapshot_id


@pytest.mark.parametrize(
    "outcome",
    [
        ExecutionOutcome(status="COMPILE_ERROR", provider_run_id="compile", compiler_output="bad"),
        ExecutionOutcome(status="RUNTIME_ERROR", provider_run_id="runtime", exit_code=139),
        ExecutionOutcome(status="TIMED_OUT", provider_run_id="timeout", timed_out=True),
        ExecutionOutcome(status="OUTPUT_LIMIT_EXCEEDED", provider_run_id="output", output_truncated=True),
        ExecutionOutcome(status="PROVIDER_ERROR", provider_run_id=None),
    ],
)
async def test_execution_failures_are_canonical_outcomes(
    db_session: AsyncSession, outcome: ExecutionOutcome
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = ExecutionService(db_session, FakeExecutorProvider(outcome))
    run, request, _ = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code="class Solution { public: int lengthOfLongestSubstring(string s) { return 0; } };",
            idempotency_key=f"run-{outcome.status}",
            client_event_id=f"client-{outcome.status}",
            client_instance_id="browser-1",
            client_sequence=1,
        )
    )
    completed = await service.complete(run.id, await service.execute(request))
    assert completed.status == outcome.status


async def test_completed_session_cannot_start_run(db_session: AsyncSession) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    now = datetime.now(UTC)
    await InterviewCompletionService(db_session, clock=lambda: now).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="end-before-run",
    )
    service = ExecutionService(db_session, FakeExecutorProvider(ExecutionOutcome("SUCCEEDED", None)))
    with pytest.raises(SessionClosed):
        await service.begin(
            RunCommand(
                session_id=development.interview_session.id,
                source_code="class Solution {};",
                idempotency_key="rejected-run",
                client_event_id="rejected-client",
                client_instance_id="browser-1",
                client_sequence=1,
            )
        )


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("cpp", "class Solution { public: int lengthOfLongestSubstring(string s) { return 3; } };"),
        ("python", "class Solution:\n    def lengthOfLongestSubstring(self, s: str) -> int:\n        return 3"),
        ("java", "class Solution { public int lengthOfLongestSubstring(String s) { return 3; } }"),
    ],
)
async def test_configured_language_owns_snapshot_and_harness(
    db_session: AsyncSession, language: str, source: str
) -> None:
    development = await create_development_interview(
        db_session, initial_stage="IMPLEMENTATION", language=language
    )
    provider = FakeExecutorProvider(ExecutionOutcome("SUCCEEDED", f"{language}-run"))
    service = ExecutionService(db_session, provider)
    run, request, created = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code=source,
            idempotency_key=f"{language}-run",
            client_event_id=f"{language}-event",
            client_instance_id="language-test",
            client_sequence=1,
        )
    )
    assert created and run.language == language and request.language == language
    assert provider.requests == []
    await service.complete(run.id, await service.execute(request))
    assert provider.requests[0].harness

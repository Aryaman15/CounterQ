"""Deterministic Stage 3A vertical-slice acceptance evaluation."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import ExecutionRun
from app.execution.models import TestResult as ExecutionTestResult
from app.execution.provider import ExecutionCaseOutcome, ExecutionOutcome, FakeExecutorProvider
from app.execution.service import ExecutionService, RunCommand
from app.interviews.dev_factory import create_development_interview
from app.observation.models import CodeSnapshot


async def test_stage3a_executes_the_exact_canonical_snapshot_and_persists_visible_results(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    source = "class Solution { public: int lengthOfLongestSubstring(string s) { return 3; } };"
    service = ExecutionService(
        db_session,
        FakeExecutorProvider(
            ExecutionOutcome(
                "SUCCEEDED",
                "stage3a-fake-run",
                cases=(
                    ExecutionCaseOutcome("visible-1", "3", "PASSED"),
                    ExecutionCaseOutcome("visible-2", "1", "PASSED"),
                    ExecutionCaseOutcome("visible-3", "3", "PASSED"),
                ),
            )
        ),
    )
    run, request, created = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code=source,
            idempotency_key="stage3a-run",
            client_event_id="stage3a-client-event",
            client_instance_id="stage3a-browser",
            client_sequence=1,
        )
    )
    assert created
    assert request.source_code == source
    completed = await service.complete(run.id, await service.execute(request))

    snapshot = await db_session.get(CodeSnapshot, run.code_snapshot_id)
    results = list(
        (
            await db_session.scalars(
                select(ExecutionTestResult).where(ExecutionTestResult.execution_run_id == run.id)
            )
        ).all()
    )
    persisted_run = await db_session.get(ExecutionRun, run.id)
    assert snapshot is not None and snapshot.source_code == source
    assert persisted_run is not None and completed.status == "SUCCEEDED"
    assert [result.status for result in results] == ["PASSED", "PASSED", "PASSED"]

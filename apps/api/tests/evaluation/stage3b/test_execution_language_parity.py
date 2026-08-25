# ruff: noqa: E501, I001

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.provider import ExecutionCaseOutcome, ExecutionOutcome, FakeExecutorProvider
from app.execution.service import ExecutionService, RunCommand
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.runtime import SessionClosed
from app.observation.models import CodeSnapshot


SOURCES = {
    "cpp": "class Solution { public: int lengthOfLongestSubstring(string s) { return 3; } };",
    "python": "class Solution:\n    def lengthOfLongestSubstring(self, s):\n        return 3",
    "java": "class Solution { public int lengthOfLongestSubstring(String s) { return 3; } }",
}


async def test_stage3b_same_execution_pipeline_preserves_all_supported_languages(
    db_session: AsyncSession,
) -> None:
    for sequence, (language, source) in enumerate(SOURCES.items(), start=1):
        development = await create_development_interview(
            db_session, initial_stage="IMPLEMENTATION", language=language
        )
        provider = FakeExecutorProvider(
            ExecutionOutcome(
                "SUCCEEDED",
                f"{language}-provider-run",
                runtime_version=f"{language}-runtime",
                cases=(ExecutionCaseOutcome("visible-1", "3", "PASSED"),),
            )
        )
        service = ExecutionService(db_session, provider)
        run, request, _ = await service.begin(
            RunCommand(
                session_id=development.interview_session.id,
                source_code=source,
                idempotency_key=f"stage3b-{language}",
                client_event_id=f"stage3b-event-{language}",
                client_instance_id="stage3b-evaluation",
                client_sequence=sequence,
            )
        )
        completed = await service.complete(run.id, await service.execute(request))
        assert request.language == language
        assert completed.language == language
        assert completed.runtime_version == f"{language}-runtime"
        snapshot = await db_session.get(CodeSnapshot, completed.code_snapshot_id)
        assert snapshot is not None and snapshot.source_code == source


@pytest.mark.parametrize("language", ("cpp", "python", "java"))
async def test_language_mismatch_is_rejected_before_execution(
    db_session: AsyncSession, language: str
) -> None:
    development = await create_development_interview(
        db_session, initial_stage="IMPLEMENTATION", language=language
    )
    service = ExecutionService(db_session, FakeExecutorProvider(ExecutionOutcome("SUCCEEDED", "fake")))
    run, _, _ = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code=SOURCES[language],
            idempotency_key=f"mismatch-{language}",
            client_event_id=f"mismatch-event-{language}",
            client_instance_id="stage3b-evaluation",
            client_sequence=1,
        )
    )
    snapshot = await db_session.get(CodeSnapshot, run.code_snapshot_id)
    assert snapshot is not None
    snapshot.language = "java" if language != "java" else "python"
    await db_session.flush()
    with pytest.raises(ValueError, match="language is inconsistent"):
        await service._request_for_run(run)


@pytest.mark.parametrize("language", ("cpp", "python", "java"))
async def test_duplicate_and_terminal_runs_preserve_language_rules(
    db_session: AsyncSession, language: str
) -> None:
    development = await create_development_interview(
        db_session, initial_stage="IMPLEMENTATION", language=language
    )
    provider = FakeExecutorProvider(ExecutionOutcome("PROVIDER_ERROR", None))
    service = ExecutionService(db_session, provider)
    command = RunCommand(
        session_id=development.interview_session.id,
        source_code=SOURCES[language],
        idempotency_key=f"duplicate-{language}",
        client_event_id=f"duplicate-event-{language}",
        client_instance_id="stage3b-evaluation",
        client_sequence=1,
    )
    run, request, created = await service.begin(command)
    assert created and request.language == language
    completed = await service.complete(run.id, await service.execute(request))
    retry, _, retry_created = await service.begin(command)
    assert completed.status == "PROVIDER_ERROR"
    assert retry.id == run.id and not retry_created and len(provider.requests) == 1

    await InterviewCompletionService(db_session, clock=lambda: datetime.now(UTC)).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key=f"complete-{language}",
    )
    with pytest.raises(SessionClosed):
        await service.begin(
            RunCommand(
                session_id=development.interview_session.id,
                source_code=SOURCES[language],
                idempotency_key=f"after-terminal-{language}",
                client_event_id=f"after-terminal-event-{language}",
                client_instance_id="stage3b-evaluation",
                client_sequence=2,
            )
        )

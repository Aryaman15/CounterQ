from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.provider import ExecutionCaseOutcome, ExecutionOutcome, FakeExecutorProvider
from app.execution.service import ExecutionService, RunCommand
from app.interviews.dev_factory import create_development_interview


async def test_stage3b_same_execution_pipeline_preserves_all_supported_languages(
    db_session: AsyncSession,
) -> None:
    sources = {
        "cpp": "class Solution { public: int lengthOfLongestSubstring(string s) { return 3; } };",
        "python": "class Solution:\n    def lengthOfLongestSubstring(self, s):\n        return 3",
        "java": "class Solution { public int lengthOfLongestSubstring(String s) { return 3; } }",
    }
    for sequence, (language, source) in enumerate(sources.items(), start=1):
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

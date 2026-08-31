from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import User
from app.config.settings import create_settings, get_settings
from app.db.session import build_engine, get_session
from app.execution.models import ExecutionRun
from app.execution.models import TestResult as ExecutionTestResult
from app.execution.provider import (
    ExecutionCaseOutcome,
    ExecutionOutcome,
    ExecutionRequest,
)
from app.execution.routes import DevelopmentRunRequest, get_executor_provider_builder
from app.execution.service import (
    ExecutionIdempotencyConflict,
    ExecutionService,
    RunCommand,
)
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.main import create_app
from app.observation.models import CodeSnapshot, InterviewEvent
from app.problems.models import InterviewPackVersion, Problem, ProblemVersion
from app.realtime.control_protocol import RealtimeDevelopmentBootstrapRequest

SOURCE_A = "class Solution { public: int lengthOfLongestSubstring(string s) { return 3; } };"
SOURCE_B = "class Solution { public: int lengthOfLongestSubstring(string s) { return 4; } };"


@dataclass(frozen=True)
class CommittedDevelopment:
    session_id: UUID
    user_id: UUID
    configuration_id: UUID
    problem_id: UUID


class DelayedExecutorProvider:
    provider_name = "integrity-fake"

    def __init__(self, delay_seconds: float = 0.1) -> None:
        self.delay_seconds = delay_seconds
        self.requests: list[ExecutionRequest] = []

    async def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        self.requests.append(request)
        await asyncio.sleep(self.delay_seconds)
        return _successful_outcome()


@pytest.fixture
async def committed_development() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], CommittedDevelopment]
]:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        development = await create_development_interview(session, initial_stage="IMPLEMENTATION")
        fixture = CommittedDevelopment(
            session_id=development.interview_session.id,
            user_id=development.user.id,
            configuration_id=development.configuration.id,
            problem_id=development.problem.id,
        )
    try:
        yield sessions, fixture
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                delete(InterviewSession).where(InterviewSession.id == fixture.session_id)
            )
            await session.execute(
                delete(InterviewConfiguration).where(
                    InterviewConfiguration.id == fixture.configuration_id
                )
            )
            await session.execute(delete(User).where(User.id == fixture.user_id))
            await session.execute(delete(Problem).where(Problem.id == fixture.problem_id))
        await engine.dispose()


def test_bootstrap_contract_forbids_server_owned_and_unknown_fields() -> None:
    base: dict[str, object] = {
        "purpose": "interview_demo",
        "problem_version_id": str(uuid4()),
        "language": "cpp",
    }
    forbidden = (
        "interview_pack_version_id",
        "starter_code",
        "method_name",
        "comparator",
        "reference_solution",
        "pack_json",
        "current_stage",
        "state_version",
        "deadline_at",
        "mode",
        "level",
    )
    for field in forbidden:
        with pytest.raises(ValidationError, match=field):
            RealtimeDevelopmentBootstrapRequest.model_validate(base | {field: "owned"})


def test_bootstrap_contract_rejects_synthetic_fixture_purpose() -> None:
    with pytest.raises(ValidationError, match="stage1_fixture"):
        RealtimeDevelopmentBootstrapRequest.model_validate({"purpose": "stage1_fixture"})


async def test_synthetic_fixture_purpose_creates_no_durable_rows(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    model_types = (InterviewSession, Problem, ProblemVersion, InterviewPackVersion)

    async def row_counts() -> tuple[int, ...]:
        counts: list[int] = []
        for model_type in model_types:
            count = await db_session.scalar(select(func.count()).select_from(model_type))
            counts.append(int(count or 0))
        return tuple(counts)

    before = await row_counts()
    settings = create_settings(env_file=tmp_path / ".env")
    settings.app_env = "local"
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/realtime/development-interview",
            json={"purpose": "stage1_fixture"},
        )

    after = await row_counts()
    assert response.status_code == 422
    assert any(error["loc"][-1] == "purpose" for error in response.json()["detail"])
    assert after == before


@pytest.mark.parametrize(
    "payload",
    [
        {"purpose": "interview_demo", "problem_version_id": str(uuid4()), "language": "cpp"},
        {"purpose": "interview_demo", "interview_session_id": str(uuid4())},
    ],
)
def test_bootstrap_create_and_restore_shapes_are_mutually_exclusive(
    payload: dict[str, object],
) -> None:
    valid = RealtimeDevelopmentBootstrapRequest.model_validate(payload)
    if valid.interview_session_id is None:
        with pytest.raises(ValidationError, match="cannot be changed"):
            RealtimeDevelopmentBootstrapRequest.model_validate(
                payload | {"interview_session_id": str(uuid4())}
            )
    else:
        with pytest.raises(ValidationError, match="cannot be changed"):
            RealtimeDevelopmentBootstrapRequest.model_validate(
                payload | {"problem_version_id": str(uuid4()), "language": "java"}
            )


async def test_candidate_pack_selection_is_rejected_at_http_boundary(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    settings = create_settings(env_file=tmp_path / ".env")
    settings.app_env = "local"
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/realtime/development-interview",
            json={
                "purpose": "interview_demo",
                "problem_version_id": str(uuid4()),
                "language": "python",
                "interview_pack_version_id": str(uuid4()),
            },
        )
    assert response.status_code == 422
    assert any(
        error["loc"][-1] == "interview_pack_version_id" for error in response.json()["detail"]
    )


async def test_conflicting_execution_retry_returns_candidate_safe_http_409(
    committed_development: tuple[async_sessionmaker[AsyncSession], CommittedDevelopment],
    tmp_path: Path,
) -> None:
    sessions, fixture = committed_development
    interview_session_id = fixture.session_id
    settings = create_settings(env_file=tmp_path / ".env")
    settings.app_env = "local"
    provider = DelayedExecutorProvider(delay_seconds=0)
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_executor_provider_builder] = lambda: lambda _settings: provider
    payload = {
        "interview_session_id": str(interview_session_id),
        "source_code": SOURCE_A,
        "idempotency_key": "http-conflict",
        "client_event_id": "http-conflict-first",
        "client_instance_id": "http-conflict-browser",
        "client_sequence": 1,
        "run_kind": "VISIBLE",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first = await client.post("/api/execution/development-runs", json=payload)
        assert first.status_code == 200, first.text
        canonical_run_id = UUID(first.json()["execution_run_id"])
        async with sessions() as inspection:
            counts_before = await _session_fact_counts(inspection, interview_session_id)
            results_before = await _result_count(inspection, canonical_run_id)

        conflict = await client.post(
            "/api/execution/development-runs",
            json=payload
            | {
                "source_code": SOURCE_B,
                "client_event_id": "http-conflict-second",
                "client_sequence": 2,
            },
        )

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == (
        "Idempotency key already represents a different execution request"
    )
    async with sessions() as inspection:
        assert await _session_fact_counts(inspection, interview_session_id) == counts_before
        assert await _result_count(inspection, canonical_run_id) == results_before
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "field",
    [
        "expected_output",
        "comparator",
        "method_name",
        "problem_version_id",
        "interview_pack_version_id",
        "language",
        "reference_solution",
    ],
)
def test_execution_contract_rejects_every_server_owned_field(field: str) -> None:
    payload: dict[str, object] = {
        "interview_session_id": str(uuid4()),
        "source_code": SOURCE_A,
        "idempotency_key": "strict-execution",
        "client_event_id": "strict-execution-event",
        "client_instance_id": "strict-execution-browser",
        "client_sequence": 1,
        field: "owned",
    }
    with pytest.raises(ValidationError, match=field):
        DevelopmentRunRequest.model_validate(payload)


async def test_exact_retry_reuses_one_durable_execution_lineage(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    provider = DelayedExecutorProvider(delay_seconds=0)
    service = ExecutionService(db_session, provider)
    first_command = _command(development.interview_session.id, key="exact-retry")
    run, request, created = await service.begin(first_command)
    assert created
    await service.complete(run.id, await service.execute(request))

    retry_command = _command(
        development.interview_session.id,
        key="exact-retry",
        client_event_id="later-transport-event",
        client_instance_id="later-browser",
        client_sequence=99,
    )
    retry, rebuilt, retry_created = await service.begin(retry_command)
    assert not retry_created
    assert retry.id == run.id
    assert retry.run_event_id == run.run_event_id
    assert retry.code_snapshot_id == run.code_snapshot_id
    assert retry.problem_version_id == run.problem_version_id
    assert retry.language == run.language
    assert rebuilt.source_code == SOURCE_A
    assert len(provider.requests) == 1
    assert await _count(db_session, ExecutionRun, run.interview_session_id) == 1
    assert await _event_count(db_session, run.interview_session_id, "RUN_CLICKED") == 1
    assert await _count(db_session, CodeSnapshot, run.interview_session_id) == 1
    assert await _result_count(db_session, run.id) == 3


@pytest.mark.parametrize(
    ("first_kind", "first_arguments", "retry_kind", "retry_arguments", "retry_source"),
    [
        ("VISIBLE", None, "VISIBLE", None, SOURCE_B),
        ("VISIBLE", None, "CUSTOM", {"s": "abc"}, SOURCE_A),
        ("CUSTOM", {"s": "abc"}, "VISIBLE", None, SOURCE_A),
        ("CUSTOM", {"s": "abc"}, "CUSTOM", {"s": "abcd"}, SOURCE_A),
    ],
)
async def test_conflicting_key_reuse_changes_no_canonical_facts(
    db_session: AsyncSession,
    first_kind: str,
    first_arguments: dict[str, object] | None,
    retry_kind: str,
    retry_arguments: dict[str, object] | None,
    retry_source: str,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    provider = DelayedExecutorProvider(delay_seconds=0)
    service = ExecutionService(db_session, provider)
    first = _command(
        development.interview_session.id,
        key="conflicting-reuse",
        run_kind=first_kind,
        custom_arguments=first_arguments,
    )
    run, request, _ = await service.begin(first)
    await service.complete(run.id, await service.execute(request))
    counts_before = await _lineage_counts(db_session, run)

    conflicting = _command(
        development.interview_session.id,
        key="conflicting-reuse",
        source=retry_source,
        run_kind=retry_kind,
        custom_arguments=retry_arguments,
        client_sequence=2,
    )
    with pytest.raises(ExecutionIdempotencyConflict):
        await service.begin(conflicting)

    assert await _lineage_counts(db_session, run) == counts_before
    assert len(provider.requests) == 1


async def test_custom_argument_dict_order_is_an_exact_retry(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    execution = development.problem_version.io_schema_json["execution"]
    assert isinstance(execution, dict)
    execution["arguments"] = [
        {"name": "left", "type": "string"},
        {"name": "right", "type": "string"},
    ]
    provider = DelayedExecutorProvider(delay_seconds=0)
    service = ExecutionService(db_session, provider)
    first = _command(
        development.interview_session.id,
        key="custom-order",
        run_kind="CUSTOM",
        custom_arguments={"left": "a", "right": "b"},
    )
    run, request, _ = await service.begin(first)
    await service.complete(run.id, await service.execute(request))

    retry, rebuilt, created = await service.begin(
        _command(
            development.interview_session.id,
            key="custom-order",
            run_kind="CUSTOM",
            custom_arguments={"right": "b", "left": "a"},
            client_sequence=2,
        )
    )
    assert not created and retry.id == run.id
    assert rebuilt.cases[0].input_json == {"left": "a", "right": "b"}
    assert len(provider.requests) == 1


async def test_concurrent_identical_requests_converge_on_one_postgres_lineage(
    committed_development: tuple[async_sessionmaker[AsyncSession], CommittedDevelopment],
) -> None:
    sessions, fixture = committed_development
    provider = DelayedExecutorProvider()
    commands = [
        _command(fixture.session_id, key="concurrent-identical", client_sequence=sequence)
        for sequence in (1, 2)
    ]
    results = await asyncio.gather(
        *(_execute_request(sessions, provider, command) for command in commands)
    )
    assert {result[0] for result in results} == {"ok"}
    assert len({result[1] for result in results}) == 1
    assert {result[2] for result in results} == {"SUCCEEDED"}
    assert sum(bool(result[3]) for result in results) == 1
    assert len(provider.requests) == 1

    async with sessions() as session:
        run = await session.scalar(
            select(ExecutionRun).where(ExecutionRun.interview_session_id == fixture.session_id)
        )
        assert run is not None
        assert await _count(session, ExecutionRun, fixture.session_id) == 1
        assert await _event_count(session, fixture.session_id, "RUN_CLICKED") == 1
        assert await _count(session, CodeSnapshot, fixture.session_id) == 1
        assert await _result_count(session, run.id) == 3


async def test_concurrent_conflicting_requests_choose_one_command_without_split_brain(
    committed_development: tuple[async_sessionmaker[AsyncSession], CommittedDevelopment],
) -> None:
    sessions, fixture = committed_development
    provider = DelayedExecutorProvider()
    results = await asyncio.gather(
        _execute_request(
            sessions,
            provider,
            _command(fixture.session_id, key="concurrent-conflict", source=SOURCE_A),
        ),
        _execute_request(
            sessions,
            provider,
            _command(
                fixture.session_id,
                key="concurrent-conflict",
                source=SOURCE_B,
                client_sequence=2,
            ),
        ),
    )
    assert sorted(result[0] for result in results) == ["conflict", "ok"]
    assert len(provider.requests) == 1

    async with sessions() as session:
        run = await session.scalar(
            select(ExecutionRun).where(ExecutionRun.interview_session_id == fixture.session_id)
        )
        assert run is not None
        snapshot = await session.get(CodeSnapshot, run.code_snapshot_id)
        assert snapshot is not None and snapshot.source_code in {SOURCE_A, SOURCE_B}
        assert await _count(session, ExecutionRun, fixture.session_id) == 1
        assert await _event_count(session, fixture.session_id, "RUN_CLICKED") == 1
        assert await _count(session, CodeSnapshot, fixture.session_id) == 1
        assert await _result_count(session, run.id) == 3


async def test_same_textual_key_is_independent_across_sessions(
    db_session: AsyncSession,
) -> None:
    first = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    second = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    provider = DelayedExecutorProvider(delay_seconds=0)
    service = ExecutionService(db_session, provider)
    first_run, _, first_created = await service.begin(
        _command(first.interview_session.id, key="shared-text")
    )
    second_run, _, second_created = await service.begin(
        _command(second.interview_session.id, key="shared-text")
    )
    assert first_created and second_created
    assert first_run.id != second_run.id


async def test_concurrent_completion_is_terminal_fact_idempotent(
    committed_development: tuple[async_sessionmaker[AsyncSession], CommittedDevelopment],
) -> None:
    sessions, fixture = committed_development
    provider = DelayedExecutorProvider(delay_seconds=0)
    async with sessions() as session:
        service = ExecutionService(session, provider)
        async with session.begin():
            run, _, _ = await service.begin(_command(fixture.session_id, key="completion-race"))
        run_id = run.id

    async def complete_once() -> UUID:
        async with sessions() as session:
            service = ExecutionService(session, provider)
            async with session.begin():
                completed = await service.complete(run_id, _successful_outcome())
            return completed.id

    completed_ids = await asyncio.gather(complete_once(), complete_once())
    assert list(completed_ids) == [run_id, run_id]
    async with sessions() as session:
        service = ExecutionService(session, provider)
        async with session.begin():
            repeated = await service.complete(
                run_id,
                ExecutionOutcome(
                    status="RUNTIME_ERROR",
                    provider_run_id="must-not-replace-terminal-facts",
                ),
            )
        assert repeated.status == "SUCCEEDED"
        assert repeated.provider_run_id == "integrity-provider-run"
    async with sessions() as session:
        loaded_run = await session.get(ExecutionRun, run_id)
        assert loaded_run is not None and loaded_run.status == "SUCCEEDED"
        assert await _event_count(session, fixture.session_id, "COMPILE_COMPLETED") == 1
        assert await _event_count(session, fixture.session_id, "TEST_COMPLETED") == 1
        assert await _result_count(session, run_id) == 3


async def test_terminal_session_rejects_execution_without_durable_side_effects(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    await InterviewCompletionService(db_session).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="integrity-terminal",
    )
    before = await _session_fact_counts(db_session, development.interview_session.id)
    with pytest.raises(ValueError, match="closed"):
        await ExecutionService(db_session, DelayedExecutorProvider(delay_seconds=0)).begin(
            _command(development.interview_session.id, key="after-terminal")
        )
    assert await _session_fact_counts(db_session, development.interview_session.id) == before


async def _execute_request(
    sessions: async_sessionmaker[AsyncSession],
    provider: DelayedExecutorProvider,
    command: RunCommand,
) -> tuple[str, UUID | None, str | None, bool]:
    async with sessions() as session:
        service = ExecutionService(session, provider)
        try:
            async with session.begin():
                run, request, created = await service.begin(command)
        except ExecutionIdempotencyConflict:
            return "conflict", None, None, False
        if created:
            outcome = await service.execute(request)
            async with session.begin():
                run = await service.complete(run.id, outcome)
        elif run.status == "RUNNING":
            run = await service.wait_for_terminal(run.id, timeout_seconds=2)
        return "ok", run.id, run.status, created


def _command(
    session_id: UUID,
    *,
    key: str,
    source: str = SOURCE_A,
    client_event_id: str = "integrity-event",
    client_instance_id: str = "integrity-browser",
    client_sequence: int = 1,
    run_kind: str = "VISIBLE",
    custom_arguments: dict[str, object] | None = None,
) -> RunCommand:
    assert run_kind in {"VISIBLE", "CUSTOM"}
    return RunCommand(
        session_id=session_id,
        source_code=source,
        idempotency_key=key,
        client_event_id=client_event_id,
        client_instance_id=client_instance_id,
        client_sequence=client_sequence,
        run_kind=run_kind,  # type: ignore[arg-type]
        custom_arguments=custom_arguments,
    )


def _successful_outcome() -> ExecutionOutcome:
    return ExecutionOutcome(
        status="SUCCEEDED",
        provider_run_id="integrity-provider-run",
        cases=(
            ExecutionCaseOutcome("visible-1", "3", "PASSED"),
            ExecutionCaseOutcome("visible-2", "1", "PASSED"),
            ExecutionCaseOutcome("visible-3", "3", "PASSED"),
        ),
    )


async def _count(
    session: AsyncSession,
    model: type[Any],
    interview_session_id: UUID,
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.interview_session_id == interview_session_id)
        )
        or 0
    )


async def _event_count(
    session: AsyncSession,
    interview_session_id: UUID,
    event_type: str,
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(InterviewEvent)
            .where(InterviewEvent.interview_session_id == interview_session_id)
            .where(InterviewEvent.event_type == event_type)
        )
        or 0
    )


async def _result_count(session: AsyncSession, run_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(ExecutionTestResult)
            .where(ExecutionTestResult.execution_run_id == run_id)
        )
        or 0
    )


async def _lineage_counts(
    session: AsyncSession,
    run: ExecutionRun,
) -> tuple[int, int, int, int, int, int]:
    return (
        await _count(session, ExecutionRun, run.interview_session_id),
        await _count(session, CodeSnapshot, run.interview_session_id),
        await _event_count(session, run.interview_session_id, "RUN_CLICKED"),
        await _event_count(session, run.interview_session_id, "COMPILE_COMPLETED"),
        await _event_count(session, run.interview_session_id, "TEST_COMPLETED"),
        await _result_count(session, run.id),
    )


async def _session_fact_counts(
    session: AsyncSession,
    interview_session_id: UUID,
) -> tuple[int, int, int]:
    return (
        await _count(session, ExecutionRun, interview_session_id),
        await _count(session, CodeSnapshot, interview_session_id),
        await _event_count(session, interview_session_id, "RUN_CLICKED"),
    )

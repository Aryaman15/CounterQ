from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload, selectinload

from app.auth.dependencies import get_current_user
from app.auth.principal import CurrentUser
from app.auth.repository import CandidateProfileRepository, UserRepository
from app.db.session import build_engine, get_session
from app.interviews.creation import SelfServeInterviewCreationService
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.interviews.repository import InterviewRepository
from app.main import create_app
from app.problems.content import load_curated_content, load_ontology
from app.problems.models import Problem
from app.problems.repository import ProblemRepository
from app.problems.service import CuratedProblemService
from app.realtime.control_protocol import (
    CandidateCodeSnapshotMessage,
    CandidateTranscriptFinalizedMessage,
)
from app.realtime.control_service import RealtimeControlService
from app.realtime.provider import RealtimeBrowserSession
from app.realtime.routes import get_realtime_voice_provider_builder
from app.realtime.tickets import ControlTicketStore, get_control_ticket_store


class FakeRealtimeProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def create_browser_session(self) -> RealtimeBrowserSession:
        self.calls += 1
        return RealtimeBrowserSession(
            provider="openai",
            client_secret="stage9b-ephemeral",
            webrtc_url="https://api.openai.com/v1/realtime/calls",
            model="gpt-realtime-test",
            voice="marin",
            transcription_model="gpt-live-transcribe",
            expires_at=datetime(2026, 9, 10, 12, 10, tzinfo=UTC),
            expires_after_seconds=600,
        )


class InMemoryTicketBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, name: str, value: str, *, ex: int, nx: bool) -> bool:
        del ex
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    async def getdel(self, name: str) -> str | None:
        return self.values.pop(name, None)


async def _seed_candidate(
    *,
    with_profile: bool = True,
    language: str = "python",
    mode: str = "COACH",
    level: str = "EARLY_CAREER",
) -> tuple[UUID, UUID]:
    engine = build_engine()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            curated = CuratedProblemService(session)
            await curated.seed_ontology(load_ontology())
            for entry in load_curated_content():
                await curated.seed_problem(entry)
            user = await UserRepository(session).add(
                external_auth_provider="stage9b",
                external_auth_subject=f"candidate-{uuid4()}",
            )
            if with_profile:
                await CandidateProfileRepository(session).upsert(
                    user_id=user.id,
                    display_name=None,
                    preferred_language=language,
                    default_interview_mode=mode,
                    interview_level=level,
                    target_role=None,
                    timezone=None,
                )
            first_problem = (await curated.list_candidate_catalog())[0]
            return user.id, first_problem.id
    finally:
        await engine.dispose()


def _app_for_user(
    user_id: UUID | None,
    *,
    provider: FakeRealtimeProvider | None = None,
    tickets: ControlTicketStore | None = None,
) -> tuple[FastAPI, AsyncEngine]:
    app = create_app()
    engine = build_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def isolated_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = isolated_session
    if user_id is not None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=user_id,
            status="ACTIVE",
        )
    if provider is not None:
        app.dependency_overrides[get_realtime_voice_provider_builder] = lambda: (
            lambda _settings: provider
        )
    if tickets is not None:
        app.dependency_overrides[get_control_ticket_store] = lambda: tickets
    return app, engine


def _create_payload(
    problem_version_id: UUID,
    *,
    template: str = "STANDARD_CODING_INTERVIEW",
    mode: str = "SIMULATION",
    language: str = "cpp",
) -> dict[str, object]:
    return {
        "problem_version_id": str(problem_version_id),
        "template": template,
        "mode": mode,
        "language": language,
    }


async def _seed_selective_problem(
    *,
    languages: tuple[str, ...],
    pack_review_status: str = "REVIEWED",
) -> UUID:
    engine = build_engine()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            repository = ProblemRepository(session)
            suffix = uuid4().hex
            problem = await repository.add_problem(
                source_type="CURATED",
                slug=f"stage9b-selective-{suffix}",
                status="ACTIVE",
            )
            version = await repository.add_problem_version(
                problem=problem,
                version="v1",
                title="Selective language problem",
                statement="Return the input.",
                content_hash=f"sha256:{suffix}",
                schema_version="problem.v1",
            )
            version.constraints_json = {"items": []}
            version.examples_json = []
            version.io_schema_json = {
                "catalog_order": 10_000 + int(suffix[:4], 16),
                "review_status": "REVIEWED",
                "languages": {
                    item: {"display_signature": "solve()", "starter_code": ""}
                    for item in languages
                },
                "execution": {
                    "arguments": [],
                    "return_type": "int",
                    "comparator": "EXACT",
                    "custom_test_supported": False,
                },
            }
            await repository.add_interview_pack_version(
                problem_version=version,
                schema_version="interview-pack.v1",
                authored_version="v1",
                pack_json={"private": "not for browser"},
                review_status=pack_review_status,
            )
            return version.id
    finally:
        await engine.dispose()


async def _cleanup_selective_problems() -> None:
    engine = build_engine()
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(
                delete(Problem).where(Problem.slug.like("stage9b-selective-%"))
            )
    finally:
        await engine.dispose()


async def test_creation_inherits_profile_and_pins_exact_policy_and_curated_versions() -> None:
    user_id, problem_version_id = await _seed_candidate()
    app, engine = _app_for_user(user_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            quick = await client.post(
                "/api/interviews",
                json=_create_payload(
                    problem_version_id,
                    template="QUICK_DRILL",
                    mode="SIMULATION",
                    language="cpp",
                ),
            )
            standard = await client.post(
                "/api/interviews",
                json=_create_payload(
                    problem_version_id,
                    template="STANDARD_CODING_INTERVIEW",
                    mode="COACH",
                    language="python",
                ),
            )

        assert quick.status_code == 201, quick.text
        assert standard.status_code == 201, standard.text
        assert quick.json()["configured_duration_seconds"] == 600
        assert standard.json()["configured_duration_seconds"] == 1800
        assert quick.json()["current_stage"] == "INTRODUCTION"
        assert standard.json()["current_stage"] == "INTRODUCTION"
        assert quick.json()["interview_path"] == (
            f"/interview/{quick.json()['interview_session_id']}"
        )

        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            sessions = list(
                (
                    await session.scalars(
                        select(InterviewSession)
                        .options(
                            joinedload(InterviewSession.configuration),
                            joinedload(InterviewSession.interview_pack_version),
                            selectinload(InterviewSession.budget),
                        )
                        .where(InterviewSession.user_id == user_id)
                        .order_by(InterviewSession.started_at)
                    )
                ).all()
            )
            profile = await CandidateProfileRepository(session).get(user_id)
        assert len(sessions) == 2
        assert all(item.problem_version_id == problem_version_id for item in sessions)
        assert all(
            item.interview_pack_version.problem_version_id == item.problem_version_id
            for item in sessions
        )
        assert [item.configuration.level for item in sessions] == [
            "EARLY_CAREER",
            "EARLY_CAREER",
        ]
        assert sessions[0].deadline_at - sessions[0].started_at == timedelta(seconds=600)
        assert sessions[1].deadline_at - sessions[1].started_at == timedelta(seconds=1800)
        assert sessions[0].budget is not None
        assert sessions[0].budget.max_assistance_interventions == 0
        assert sessions[1].budget is not None
        assert sessions[1].budget.max_assistance_interventions == 6
        assert profile is not None
        assert profile.preferred_language == "python"
        assert profile.default_interview_mode == "COACH"
    finally:
        await engine.dispose()


async def test_profile_is_required_and_server_owned_fields_are_forbidden() -> None:
    user_id, problem_version_id = await _seed_candidate(with_profile=False)
    app, engine = _app_for_user(user_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            missing_profile = await client.post(
                "/api/interviews", json=_create_payload(problem_version_id)
            )
            smuggled = await client.post(
                "/api/interviews",
                json={
                    **_create_payload(problem_version_id),
                    "user_id": str(uuid4()),
                    "interview_level": "INTERN",
                    "duration_seconds": 99_999,
                    "interview_pack_version_id": str(uuid4()),
                    "deadline_at": "2099-01-01T00:00:00Z",
                    "budget": {"max_probes": 999},
                },
            )
            unsupported_template = await client.post(
                "/api/interviews",
                json=_create_payload(problem_version_id, template="SOLUTION_DEFENSE"),
            )
        assert missing_profile.status_code == 409
        assert missing_profile.json()["detail"]["category"] == "onboarding_required"
        assert smuggled.status_code == 422
        assert unsupported_template.status_code == 422
    finally:
        await engine.dispose()


async def test_language_compatibility_and_reviewed_pack_gate_are_server_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _cleanup_selective_problems()
    user_id, _ = await _seed_candidate()
    cpp_only = await _seed_selective_problem(languages=("cpp",))
    unreviewed = await _seed_selective_problem(
        languages=("cpp", "python"), pack_review_status="DRAFT"
    )
    wrong_problem = await _seed_selective_problem(languages=("cpp", "python"))
    lookup_engine = build_engine()
    try:
        async with async_sessionmaker(lookup_engine)() as lookup_session:
            wrong_pack = await CuratedProblemService(
                lookup_session
            ).reviewed_pack_for_problem(wrong_problem)
    finally:
        await lookup_engine.dispose()
    app, engine = _app_for_user(user_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            unsupported_language = await client.post(
                "/api/interviews",
                json=_create_payload(cpp_only, language="python"),
            )
            unreviewed_pack = await client.post(
                "/api/interviews",
                json=_create_payload(unreviewed, language="cpp"),
            )

            async def return_mismatched_pack(
                _service: CuratedProblemService,
                _problem_version_id: UUID,
            ) -> object:
                return wrong_pack

            monkeypatch.setattr(
                CuratedProblemService,
                "reviewed_pack_for_problem",
                return_mismatched_pack,
            )
            mismatched_pack = await client.post(
                "/api/interviews",
                json=_create_payload(cpp_only, language="cpp"),
            )
        assert unsupported_language.status_code == 422
        assert unsupported_language.json()["detail"]["category"] == (
            "interview_selection_invalid"
        )
        assert unreviewed_pack.status_code == 422
        assert mismatched_pack.status_code == 422
    finally:
        await engine.dispose()
        await _cleanup_selective_problems()


async def test_unavailable_curated_problem_is_rejected_without_partial_rows() -> None:
    user_id, _problem_version_id = await _seed_candidate()
    engine = build_engine()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            configurations_before = int(
                await session.scalar(select(func.count()).select_from(InterviewConfiguration)) or 0
            )
            sessions_before = int(
                await session.scalar(select(func.count()).select_from(InterviewSession)) or 0
            )
            await session.rollback()
            with pytest.raises(ValueError):
                await SelfServeInterviewCreationService(session).create(
                    user_id=user_id,
                    problem_version_id=uuid4(),
                    template="QUICK_DRILL",
                    mode="SIMULATION",
                    language="java",
                )
        async with async_sessionmaker(engine)() as session:
            assert int(
                await session.scalar(select(func.count()).select_from(InterviewConfiguration)) or 0
            ) == configurations_before
            assert int(
                await session.scalar(select(func.count()).select_from(InterviewSession)) or 0
            ) == sessions_before
    finally:
        await engine.dispose()


async def test_configuration_session_and_budget_roll_back_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, problem_version_id = await _seed_candidate()
    engine = build_engine()
    original_add_budget = InterviewRepository.add_budget

    async def fail_budget(self: InterviewRepository, **kwargs: object) -> None:
        del self, kwargs
        raise RuntimeError("injected budget failure")

    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            configurations_before = int(
                await session.scalar(select(func.count()).select_from(InterviewConfiguration)) or 0
            )
            sessions_before = int(
                await session.scalar(select(func.count()).select_from(InterviewSession)) or 0
            )
            await session.rollback()
            monkeypatch.setattr(InterviewRepository, "add_budget", fail_budget)
            with pytest.raises(RuntimeError, match="injected budget failure"):
                await SelfServeInterviewCreationService(session).create(
                    user_id=user_id,
                    problem_version_id=problem_version_id,
                    template="QUICK_DRILL",
                    mode="SIMULATION",
                    language="python",
                )
            monkeypatch.setattr(InterviewRepository, "add_budget", original_add_budget)
        async with async_sessionmaker(engine)() as session:
            assert int(
                await session.scalar(select(func.count()).select_from(InterviewConfiguration)) or 0
            ) == configurations_before
            assert int(
                await session.scalar(select(func.count()).select_from(InterviewSession)) or 0
            ) == sessions_before
    finally:
        await engine.dispose()


async def test_production_restore_preserves_deadline_state_and_hides_foreign_session() -> None:
    owner_id, problem_version_id = await _seed_candidate()
    other_id, _ = await _seed_candidate()
    owner_app, owner_engine = _app_for_user(owner_id)
    other_app, other_engine = _app_for_user(other_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=owner_app), base_url="http://test"
        ) as owner_client:
            created_response = await owner_client.post(
                "/api/interviews", json=_create_payload(problem_version_id)
            )
        created = created_response.json()
        session_id = UUID(created["interview_session_id"])
        sessionmaker = async_sessionmaker(owner_engine, expire_on_commit=False)
        async with sessionmaker() as session, session.begin():
            service = RealtimeControlService(session)
            await service.persist_candidate_transcript(
                session_id=session_id,
                message=CandidateTranscriptFinalizedMessage(
                    client_event_id="stage9b-transcript-1",
                    client_instance_id="stage9b-browser",
                    client_sequence=1,
                    type="candidate_transcript_finalized",
                    provider_item_id="candidate-item-1",
                    transcript="I will begin with a sliding window.",
                    idempotency_key="stage9b-transcript-1",
                ),
            )
            await service.persist_candidate_code_snapshot(
                session_id=session_id,
                message=CandidateCodeSnapshotMessage(
                    client_event_id="stage9b-code-2",
                    client_instance_id="stage9b-browser",
                    client_sequence=2,
                    type="candidate_code_snapshot",
                    source_code="class Solution: pass",
                    language="cpp",
                    trigger="EDIT_BURST",
                    idempotency_key="stage9b-code-2",
                ),
            )
            unresolved = await service.create_development_authorized_prompt(
                session_id=session_id
            )

        async with AsyncClient(
            transport=ASGITransport(app=owner_app), base_url="http://test"
        ) as owner_client:
            first = await owner_client.post(
                f"/api/interviews/{session_id}/restore",
                json={"client_instance_id": "stage9b-browser"},
            )
            second = await owner_client.post(
                f"/api/interviews/{session_id}/restore",
                json={"client_instance_id": "stage9b-browser"},
            )
        async with AsyncClient(
            transport=ASGITransport(app=other_app), base_url="http://test"
        ) as other_client:
            foreign = await other_client.post(
                f"/api/interviews/{session_id}/restore",
                json={"client_instance_id": "other-browser"},
            )
            foreign_run = await other_client.post(
                f"/api/execution/interviews/{session_id}/runs",
                json={
                    "source_code": "return 1;",
                    "idempotency_key": "foreign-run",
                    "client_event_id": "foreign-run",
                    "client_instance_id": "other-browser",
                    "client_sequence": 1,
                    "run_kind": "VISIBLE",
                },
            )

        assert first.status_code == second.status_code == 200
        assert first.json()["interview_session_id"] == created["interview_session_id"]
        assert first.json()["started_at"] == second.json()["started_at"] == created["started_at"]
        assert first.json()["deadline_at"] == second.json()["deadline_at"] == created["deadline_at"]
        assert second.json()["time_remaining_seconds"] <= first.json()["time_remaining_seconds"]
        assert first.json()["latest_code_snapshot"]["source_code"] == "class Solution: pass"
        assert first.json()["recent_conversation"][0]["text"] == (
            "I will begin with a sliding window."
        )
        assert first.json()["highest_client_sequence"] == 2
        assert first.json()["unresolved_prompt"] == {
            "id": str(unresolved.prompt_id),
            "kind": "INSTRUCTION",
            "status": "AUTHORIZED",
        }
        assert first.json()["control_websocket_path"] == f"/api/realtime/control/{session_id}"
        assert "pack" not in str(first.json()).lower()
        assert foreign.status_code == 404
        assert foreign.json() == {"detail": "Interview session was not found"}
        assert foreign_run.status_code == 404

        sessionmaker = async_sessionmaker(owner_engine, expire_on_commit=False)
        async with sessionmaker() as session, session.begin():
            interview = await session.get(InterviewSession, session_id)
            assert interview is not None
            interview.status = "COMPLETED"
            interview.completed_at = datetime.now(UTC)
        async with AsyncClient(
            transport=ASGITransport(app=owner_app), base_url="http://test"
        ) as owner_client:
            terminal = await owner_client.post(
                f"/api/interviews/{session_id}/restore",
                json={"client_instance_id": "stage9b-browser"},
            )
        assert terminal.status_code == 200
        assert terminal.json()["session_status"] == "COMPLETED"
        assert terminal.json()["deadline_at"] == created["deadline_at"]
    finally:
        await owner_engine.dispose()
        await other_engine.dispose()


async def test_realtime_credential_and_control_ticket_require_active_owner() -> None:
    owner_id, problem_version_id = await _seed_candidate()
    other_id, _ = await _seed_candidate()
    provider = FakeRealtimeProvider()
    ticket_store = ControlTicketStore(InMemoryTicketBackend(), ttl_seconds=60)
    owner_app, owner_engine = _app_for_user(
        owner_id, provider=provider, tickets=ticket_store
    )
    other_app, other_engine = _app_for_user(
        other_id, provider=provider, tickets=ticket_store
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=owner_app), base_url="http://test"
        ) as owner_client:
            created = (
                await owner_client.post(
                    "/api/interviews", json=_create_payload(problem_version_id)
                )
            ).json()
            session_id = created["interview_session_id"]
            credential = await owner_client.post(
                f"/api/realtime/interviews/{session_id}/session"
            )
            ticket = await owner_client.post(
                f"/api/realtime/interviews/{session_id}/control-ticket"
            )
        async with AsyncClient(
            transport=ASGITransport(app=other_app), base_url="http://test"
        ) as other_client:
            foreign_credential = await other_client.post(
                f"/api/realtime/interviews/{session_id}/session"
            )
            foreign_ticket = await other_client.post(
                f"/api/realtime/interviews/{session_id}/control-ticket"
            )

        assert credential.status_code == 200
        assert credential.json()["client_secret"] == "stage9b-ephemeral"
        assert provider.calls == 1
        assert ticket.status_code == 200
        assert ticket.json()["control_websocket_path"] == f"/api/realtime/control/{session_id}"
        assert "Bearer" not in ticket.json()["ticket"]
        assert foreign_credential.status_code == 404
        assert foreign_ticket.status_code == 404
        sessionmaker = async_sessionmaker(owner_engine, expire_on_commit=False)
        async with sessionmaker() as session, session.begin():
            interview = await session.get(InterviewSession, UUID(session_id))
            assert interview is not None
            interview.status = "COMPLETED"
            interview.completed_at = datetime.now(UTC)
        async with AsyncClient(
            transport=ASGITransport(app=owner_app), base_url="http://test"
        ) as owner_client:
            terminal_credential = await owner_client.post(
                f"/api/realtime/interviews/{session_id}/session"
            )
        assert terminal_credential.status_code == 404
        assert provider.calls == 1
    finally:
        await owner_engine.dispose()
        await other_engine.dispose()

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.auth.principal import CurrentUser
from app.auth.repository import CandidateProfileRepository
from app.config.settings import Settings, get_settings
from app.db.session import build_engine, get_session
from app.interviews.dev_factory import create_development_interview
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.interviews.repository import InterviewRepository
from app.main import create_app
from app.mastery.models import RetestAttempt
from app.problems.models import ProblemVersion
from app.retests.development import DEVELOPMENT_RETEST_SUBJECT


def _app_for_user(user_id: UUID) -> tuple[FastAPI, AsyncEngine]:
    app = create_app()
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def isolated_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id,
        status="ACTIVE",
    )
    app.dependency_overrides[get_session] = isolated_session
    return app, engine


async def _reset_development_retest_fixture() -> None:
    engine = build_engine()
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(
                delete(User).where(
                    User.external_auth_provider == "dev",
                    User.external_auth_subject == DEVELOPMENT_RETEST_SUBJECT,
                )
            )
    finally:
        await engine.dispose()


async def _history_fixture() -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
    engine = build_engine()
    now = datetime.now(UTC)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            base = await create_development_interview(
                session,
                now=now - timedelta(hours=5),
                template="STANDARD_CODING_INTERVIEW",
                language="cpp",
                mode="SIMULATION",
            )
            await CandidateProfileRepository(session).upsert(
                user_id=base.user.id,
                display_name="History Candidate",
                preferred_language="cpp",
                default_interview_mode="SIMULATION",
                interview_level="NEW_GRAD",
                target_role=None,
                timezone="UTC",
            )
            base.interview_session.status = "ABANDONED"
            base.interview_session.completed_at = now - timedelta(hours=4)
            repository = InterviewRepository(session)

            async def add(
                *,
                started_at: datetime,
                duration: int,
                status: str,
                mode: str,
                language: str,
            ) -> InterviewSession:
                configuration = await repository.add_configuration(
                    mode=mode,
                    level="EARLY_CAREER",
                    language=language,
                    configured_duration_seconds=duration,
                    problem_source="DEVELOPMENT_FIXTURE",
                )
                interview = await repository.add_session(
                    user_id=base.user.id,
                    configuration_id=configuration.id,
                    problem_version_id=base.problem_version.id,
                    interview_pack_version_id=base.pack_version.id,
                    current_stage="IMPLEMENTATION" if status == "ACTIVE" else "COMPLETED",
                    state_version=2,
                    status=status,
                    started_at=started_at,
                    deadline_at=started_at + timedelta(seconds=duration),
                )
                if status in {"COMPLETED", "ABANDONED"}:
                    interview.completed_at = started_at + timedelta(minutes=9)
                return interview

            completed = await add(
                started_at=now - timedelta(hours=3),
                duration=1800,
                status="COMPLETED",
                mode="COACH",
                language="python",
            )
            active = await add(
                started_at=now - timedelta(minutes=8),
                duration=1800,
                status="ACTIVE",
                mode="SIMULATION",
                language="java",
            )
            deletion_pending = await add(
                started_at=now - timedelta(minutes=2),
                duration=600,
                status="DELETION_PENDING",
                mode="SIMULATION",
                language="cpp",
            )
            foreign = await create_development_interview(
                session,
                now=now - timedelta(minutes=1),
            )
            return (
                base.user.id,
                foreign.user.id,
                active.id,
                completed.id,
                deletion_pending.id,
                base.configuration.id,
            )
    finally:
        await engine.dispose()


async def test_history_is_current_user_scoped_filtered_bounded_and_newest_first() -> None:
    owner_id, foreign_id, active_id, completed_id, deletion_id, _ = await _history_fixture()
    app, engine = _app_for_user(owner_id)
    foreign_app, foreign_engine = _app_for_user(foreign_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            all_rows = await client.get("/api/interviews?state=all&limit=50&offset=0")
            active_rows = await client.get("/api/interviews?state=in_progress")
            completed_rows = await client.get("/api/interviews?state=completed")
            selected_user = await client.get(f"/api/interviews?user_id={foreign_id}")
            unbounded = await client.get("/api/interviews?limit=51")
        async with AsyncClient(
            transport=ASGITransport(app=foreign_app),
            base_url="http://test",
        ) as client:
            foreign_rows = await client.get("/api/interviews")
    finally:
        await engine.dispose()
        await foreign_engine.dispose()

    assert all_rows.status_code == 200, all_rows.text
    payload = all_rows.json()
    ids = [row["interview_session_id"] for row in payload["items"]]
    assert ids[:2] == [str(active_id), str(completed_id)]
    assert str(deletion_id) not in ids
    assert payload["items"][0] == {
        **payload["items"][0],
        "problem_title": "Longest Substring Without Repeating Characters",
        "template": "STANDARD_CODING_INTERVIEW",
        "mode": "SIMULATION",
        "language": "java",
        "display_status": "IN_PROGRESS",
        "can_resume": True,
        "interview_path": f"/interview/{active_id}",
        "report_path": f"/interview/{active_id}/report",
        "countermap_path": f"/interview/{active_id}/countermap",
    }
    assert [row["interview_session_id"] for row in active_rows.json()["items"]] == [
        str(active_id)
    ]
    assert [row["interview_session_id"] for row in completed_rows.json()["items"]] == [
        str(completed_id)
    ]
    assert selected_user.status_code == 422
    assert unbounded.status_code == 422
    assert {row["interview_session_id"] for row in foreign_rows.json()["items"]}.isdisjoint(ids)


async def test_profile_update_persists_without_mutating_existing_configuration() -> None:
    owner_id, _foreign_id, _active_id, _completed_id, _deletion_id, configuration_id = (
        await _history_fixture()
    )
    app, engine = _app_for_user(owner_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            updated = await client.put(
                "/api/me/profile",
                json={
                    "display_name": "Updated Candidate",
                    "preferred_language": "python",
                    "default_interview_mode": "COACH",
                    "interview_level": "EARLY_CAREER",
                    "target_role": "Platform engineer",
                    "timezone": "Asia/Kolkata",
                },
            )
            reread = await client.get("/api/me")
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            configuration = await session.get(InterviewConfiguration, configuration_id)
    finally:
        await engine.dispose()

    assert updated.status_code == 200
    assert reread.json()["profile"]["preferred_language"] == "python"
    assert reread.json()["profile"]["default_interview_mode"] == "COACH"
    assert configuration is not None
    assert configuration.language == "cpp"
    assert configuration.mode == "SIMULATION"
    assert configuration.level == "NEW_GRAD"
    assert configuration.configured_duration_seconds == 1800


async def test_current_user_mastery_empty_and_arbitrary_user_selection_is_ignored() -> None:
    owner_id, foreign_id, *_ = await _history_fixture()
    app, engine = _app_for_user(foreign_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/mastery/me?user_id={owner_id}")
    finally:
        await engine.dispose()

    assert response.status_code == 200
    assert response.json()["status"] == "EMPTY"
    assert response.json()["technical_concepts"] == []
    assert response.json()["interview_skills"] == []


async def test_production_mastery_and_retest_start_preserve_stage8b_semantics() -> None:
    await _reset_development_retest_fixture()
    fixture_app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=fixture_app),
        base_url="http://test",
    ) as client:
        fixture = await client.post("/api/retests/development/fixture")
    assert fixture.status_code == 200, fixture.text
    user_id = UUID(fixture.json()["user_id"])
    recommendation_id = fixture.json()["recommendation_id"]

    owner_app, owner_engine = _app_for_user(user_id)
    other_user_id = uuid4()
    foreign_app, foreign_engine = _app_for_user(other_user_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=foreign_app),
            base_url="http://test",
        ) as client:
            foreign = await client.post(
                f"/api/retests/recommendations/{recommendation_id}/start"
            )
        async with AsyncClient(
            transport=ASGITransport(app=owner_app),
            base_url="http://test",
        ) as client:
            mastery = await client.get("/api/mastery/me")
            first = await client.post(
                f"/api/retests/recommendations/{recommendation_id}/start"
            )
            second = await client.post(
                f"/api/retests/recommendations/{recommendation_id}/start"
            )

        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            attempt_count = await session.scalar(
                select(func.count(RetestAttempt.id)).where(
                    RetestAttempt.retest_recommendation_id == UUID(recommendation_id)
                )
            )
            latest_source_problem_id = await session.scalar(
                select(ProblemVersion.problem_id)
                .join(InterviewSession, InterviewSession.problem_version_id == ProblemVersion.id)
                .where(
                    InterviewSession.user_id == user_id,
                    InterviewSession.status == "COMPLETED",
                )
                .order_by(InterviewSession.completed_at.desc(), InterviewSession.id.desc())
                .limit(1)
            )
    finally:
        await owner_engine.dispose()
        await foreign_engine.dispose()
        await _reset_development_retest_fixture()

    assert foreign.status_code == 404
    assert foreign.json()["detail"]["message"] == "This retest is unavailable."
    assert mastery.status_code == 200
    assert mastery.json()["status"] == "READY"
    assert recommendation_id in {
        item["recommendation_id"] for item in mastery.json()["retest_recommendations"]
    }
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    launch = first.json()
    assert launch["interview_path"] == f"/interview/{launch['interview_session_id']}"
    assert launch["configured_duration_seconds"] == 600
    assert launch["template"] == "QUICK_DRILL"
    assert launch["mode"] == "SIMULATION"
    assert launch["problem_id"] != str(latest_source_problem_id)
    assert launch["resumed"] is False
    assert second.json()["resumed"] is True
    assert second.json()["interview_session_id"] == launch["interview_session_id"]
    assert second.json()["retest_attempt_id"] == launch["retest_attempt_id"]
    assert attempt_count == 1


async def test_production_report_countermap_and_node_detail_hide_foreign_session() -> None:
    owner_id, foreign_id, _active_id, completed_id, *_ = await _history_fixture()
    owner_app, owner_engine = _app_for_user(owner_id)
    foreign_app, foreign_engine = _app_for_user(foreign_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=owner_app),
            base_url="http://test",
        ) as client:
            report = await client.get(f"/api/reports/sessions/{completed_id}")
            countermap = await client.get(f"/api/countermap/sessions/{completed_id}")
        async with AsyncClient(
            transport=ASGITransport(app=foreign_app),
            base_url="http://test",
        ) as client:
            foreign_report = await client.get(f"/api/reports/sessions/{completed_id}")
            foreign_map = await client.get(f"/api/countermap/sessions/{completed_id}")
            foreign_detail = await client.get(
                f"/api/countermap/sessions/{completed_id}/nodes/guessable-node"
            )
    finally:
        await owner_engine.dispose()
        await foreign_engine.dispose()

    assert report.status_code == 200
    assert countermap.status_code == 200
    assert foreign_report.status_code == 404
    assert foreign_map.status_code == 404
    assert foreign_detail.status_code == 404
    assert "another user" not in foreign_report.text.lower()


async def test_current_user_product_apis_require_authentication_in_production() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(app_env="production")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        history_response = await client.get("/api/interviews")
        mastery_response = await client.get("/api/mastery/me")
        retest_response = await client.post(
            f"/api/retests/recommendations/{uuid4()}/start"
        )

    assert history_response.status_code == 401
    assert mastery_response.status_code == 401
    assert retest_response.status_code == 401

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.auth.dependencies import get_current_user
from app.auth.principal import CurrentUser
from app.config.settings import Settings, get_settings
from app.db.session import build_engine, get_session
from app.interviews.authorization import InterviewOwnershipRepository, OwnedInterviewNotFound
from app.interviews.dev_factory import create_development_interview
from app.main import create_app


async def _completed_interview_pair() -> tuple[UUID, UUID, UUID, UUID]:
    engine = build_engine()
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            first = await create_development_interview(session)
            second = await create_development_interview(session)
            completed_at = datetime.now(UTC)
            first.interview_session.status = "COMPLETED"
            first.interview_session.completed_at = completed_at
            second.interview_session.status = "COMPLETED"
            second.interview_session.completed_at = completed_at
            return (
                first.user.id,
                first.interview_session.id,
                second.user.id,
                second.interview_session.id,
            )
    finally:
        await engine.dispose()


def _app_for_user(user_id: UUID) -> tuple[FastAPI, AsyncEngine]:
    app = create_app()
    engine = build_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def isolated_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id,
        status="ACTIVE",
    )
    app.dependency_overrides[get_session] = isolated_session
    return app, engine


async def test_report_and_countermap_are_scoped_to_authenticated_owner() -> None:
    owner_id, owner_session_id, other_id, _other_session_id = await _completed_interview_pair()
    owner_app, owner_engine = _app_for_user(owner_id)
    other_app, other_engine = _app_for_user(other_id)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=owner_app),
            base_url="http://test",
        ) as owner_client:
            report = await owner_client.get(f"/api/reports/sessions/{owner_session_id}")
            countermap = await owner_client.get(f"/api/countermap/sessions/{owner_session_id}")
        async with AsyncClient(
            transport=ASGITransport(app=other_app),
            base_url="http://test",
        ) as other_client:
            foreign_report = await other_client.get(
                f"/api/reports/sessions/{owner_session_id}"
            )
            foreign_countermap = await other_client.get(
                f"/api/countermap/sessions/{owner_session_id}"
            )
            foreign_node = await other_client.get(
                f"/api/countermap/sessions/{owner_session_id}/nodes/guessable-node"
            )
            foreign_assistance = await other_client.post(
                f"/api/interviews/{owner_session_id}/assistance-requests",
                json={"idempotency_key": uuid4().hex},
            )
    finally:
        await owner_engine.dispose()
        await other_engine.dispose()

    assert report.status_code == 200
    assert countermap.status_code == 200
    assert foreign_report.status_code == 404
    assert foreign_countermap.status_code == 404
    assert foreign_node.status_code == 404
    assert foreign_assistance.status_code == 404
    assert foreign_report.json() == {"detail": "Interview session was not found"}


async def test_reusable_ownership_query_hides_missing_foreign_and_wrong_status(
    db_session: AsyncSession,
) -> None:
    owned = await create_development_interview(db_session)
    foreign = await create_development_interview(db_session)
    repository = InterviewOwnershipRepository(db_session)

    assert (
        await repository.get_owned(
            principal_user_id=owned.user.id,
            interview_session_id=owned.interview_session.id,
            allowed_statuses=("ACTIVE",),
        )
    ).id == owned.interview_session.id
    for user_id, session_id, statuses in (
        (owned.user.id, foreign.interview_session.id, None),
        (foreign.user.id, owned.interview_session.id, None),
        (owned.user.id, owned.interview_session.id, ("COMPLETED",)),
    ):
        try:
            await repository.get_owned(
                principal_user_id=user_id,
                interview_session_id=session_id,
                allowed_statuses=statuses,
            )
        except OwnedInterviewNotFound:
            pass
        else:
            raise AssertionError("Unowned or ineligible interview became visible")


async def test_production_candidate_routes_never_fall_back_to_development_principal() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(app_env="production")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me = await client.get("/api/me")
        dev_interview = await client.post(
            "/api/realtime/development-interview",
            json={
                "interview_session_id": str(uuid4()),
                "client_instance_id": str(uuid4()),
            },
        )
        dev_report = await client.get(f"/api/reports/development/sessions/{uuid4()}")

    assert me.status_code == 401
    assert dev_interview.status_code == 403
    assert dev_report.status_code == 403


async def test_development_candidate_aliases_remain_available_in_test_environment() -> None:
    owner_id, owner_session_id, _other_id, _other_session_id = await _completed_interview_pair()
    app, engine = _app_for_user(owner_id)
    app.dependency_overrides[get_settings] = lambda: Settings(app_env="test")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            report = await client.get(
                f"/api/reports/development/sessions/{owner_session_id}"
            )
            countermap = await client.get(
                f"/api/countermap/development/sessions/{owner_session_id}"
            )
    finally:
        await engine.dispose()

    assert report.status_code == 200
    assert countermap.status_code == 200


def test_development_websocket_path_is_closed_in_production() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(app_env="production")

    with TestClient(app) as client:
        try:
            with client.websocket_connect(f"/api/realtime/development/control/{uuid4()}"):
                raise AssertionError("Production accepted a development realtime connection")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008

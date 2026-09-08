from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from app.auth.dependencies import get_current_user
from app.auth.principal import CurrentUser
from app.config.settings import get_settings
from app.db.session import build_engine, get_session
from app.interviews.dev_factory import create_development_interview
from app.main import create_app
from app.realtime.routes import get_realtime_sessionmaker
from app.realtime.tickets import (
    ControlTicketClaims,
    ControlTicketStore,
    get_control_ticket_store,
)


class InMemoryAtomicTicketBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> bool:
        del ex
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    async def getdel(self, name: str) -> str | None:
        return self.values.pop(name, None)

    def expire_all(self) -> None:
        self.values.clear()


def _store() -> tuple[ControlTicketStore, InMemoryAtomicTicketBackend]:
    backend = InMemoryAtomicTicketBackend()
    return ControlTicketStore(backend, ttl_seconds=60), backend


async def _active_interviews() -> tuple[UUID, UUID, UUID, UUID]:
    engine = build_engine()
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            first = await create_development_interview(session)
            second = await create_development_interview(session)
            return (
                first.user.id,
                first.interview_session.id,
                second.user.id,
                second.interview_session.id,
            )
    finally:
        await engine.dispose()


def _ticket_app(
    store: ControlTicketStore,
    user_id: UUID | None = None,
) -> tuple[FastAPI, AsyncEngine]:
    app = create_app()
    engine = build_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def isolated_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_control_ticket_store] = lambda: store
    app.dependency_overrides[get_session] = isolated_session
    if user_id is not None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=user_id,
            status="ACTIVE",
        )
    return app, engine


def _websocket_app(store: ControlTicketStore) -> tuple[FastAPI, AsyncEngine]:
    app = create_app()
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    app.dependency_overrides[get_control_ticket_store] = lambda: store
    app.dependency_overrides[get_realtime_sessionmaker] = lambda: sessionmaker
    return app, engine


async def test_control_ticket_is_opaque_hashed_bound_and_single_use() -> None:
    store, backend = _store()
    user_id = uuid4()
    session_id = uuid4()

    issued = await store.issue(user_id=user_id, interview_session_id=session_id)

    assert issued.expires_after_seconds == 60
    assert issued.token not in "".join(backend.values)
    assert issued.token not in "".join(backend.values.keys())
    assert len(backend.values) == 1
    assert await store.consume(issued.token) == ControlTicketClaims(
        user_id=user_id,
        interview_session_id=session_id,
    )
    assert await store.consume(issued.token) is None


async def test_expired_malformed_and_unknown_tickets_are_rejected() -> None:
    store, backend = _store()
    issued = await store.issue(user_id=uuid4(), interview_session_id=uuid4())
    backend.expire_all()

    assert await store.consume(issued.token) is None
    assert await store.consume("too-short") is None
    assert await store.consume("x" * 257) is None
    assert await store.consume("x" * 48) is None


async def test_control_ticket_minting_requires_owner_and_authentication() -> None:
    owner_id, owner_session_id, other_id, _other_session_id = await _active_interviews()
    store, _backend = _store()
    owner_app, owner_engine = _ticket_app(store, owner_id)
    other_app, other_engine = _ticket_app(store, other_id)
    unauthenticated_app, unauthenticated_engine = _ticket_app(store)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=owner_app),
            base_url="http://test",
        ) as client:
            owner_response = await client.post(
                f"/api/realtime/interviews/{owner_session_id}/control-ticket"
            )
        async with AsyncClient(
            transport=ASGITransport(app=other_app),
            base_url="http://test",
        ) as client:
            other_response = await client.post(
                f"/api/realtime/interviews/{owner_session_id}/control-ticket"
            )
        async with AsyncClient(
            transport=ASGITransport(app=unauthenticated_app),
            base_url="http://test",
        ) as client:
            unauthenticated_response = await client.post(
                f"/api/realtime/interviews/{owner_session_id}/control-ticket"
            )
    finally:
        await owner_engine.dispose()
        await other_engine.dispose()
        await unauthenticated_engine.dispose()

    assert owner_response.status_code == 200
    assert owner_response.json()["control_websocket_path"] == (
        f"/api/realtime/control/{owner_session_id}"
    )
    assert owner_response.json()["expires_after_seconds"] == 60
    assert other_response.status_code == 404
    assert unauthenticated_response.status_code == 401


def test_authenticated_websocket_accepts_once_and_rejects_replay() -> None:
    owner_id, session_id, _other_id, _other_session_id = asyncio.run(_active_interviews())
    store, _backend = _store()
    ticket = asyncio.run(store.issue(user_id=owner_id, interview_session_id=session_id)).token
    app, engine = _websocket_app(store)

    try:
        with TestClient(app) as client:
            with client.websocket_connect(
                f"/api/realtime/control/{session_id}?ticket={ticket}"
            ) as websocket:
                hello = websocket.receive_json()
                assert hello["type"] == "server_hello"
                assert hello["interview_session_id"] == str(session_id)
            try:
                with client.websocket_connect(
                    f"/api/realtime/control/{session_id}?ticket={ticket}"
                ):
                    raise AssertionError("A consumed ticket was accepted twice")
            except WebSocketDisconnect as exc:
                assert exc.code == 1008
    finally:
        asyncio.run(engine.dispose())


def test_websocket_rejects_missing_wrong_session_and_wrong_owner_tickets() -> None:
    owner_id, session_id, other_id, other_session_id = asyncio.run(_active_interviews())
    store, _backend = _store()
    wrong_session_ticket = asyncio.run(
        store.issue(user_id=owner_id, interview_session_id=session_id)
    ).token
    wrong_owner_ticket = asyncio.run(
        store.issue(user_id=other_id, interview_session_id=session_id)
    ).token
    app, engine = _websocket_app(store)

    urls = (
        f"/api/realtime/control/{session_id}",
        f"/api/realtime/control/{other_session_id}?ticket={wrong_session_ticket}",
        f"/api/realtime/control/{session_id}?ticket={wrong_owner_ticket}",
    )
    try:
        with TestClient(app) as client:
            for url in urls:
                try:
                    with client.websocket_connect(url):
                        raise AssertionError("An invalid realtime ticket was accepted")
                except WebSocketDisconnect as exc:
                    assert exc.code == 1008
    finally:
        asyncio.run(engine.dispose())

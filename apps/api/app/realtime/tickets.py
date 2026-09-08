from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import Depends
from redis.asyncio import Redis

from app.config.settings import Settings, get_settings
from app.redis.client import build_redis

TICKET_KEY_PREFIX = "counterq:realtime-control-ticket:"


class AtomicTicketBackend(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> object: ...

    async def getdel(self, name: str) -> str | None: ...


@dataclass(frozen=True)
class IssuedControlTicket:
    token: str
    expires_after_seconds: int


@dataclass(frozen=True)
class ControlTicketClaims:
    user_id: UUID
    interview_session_id: UUID


class ControlTicketStore:
    """Redis-backed, hashed, single-use realtime control authorization tickets."""

    def __init__(self, backend: AtomicTicketBackend, *, ttl_seconds: int) -> None:
        self._backend = backend
        self._ttl_seconds = ttl_seconds

    async def issue(self, *, user_id: UUID, interview_session_id: UUID) -> IssuedControlTicket:
        payload = json.dumps(
            {"user_id": str(user_id), "interview_session_id": str(interview_session_id)},
            separators=(",", ":"),
            sort_keys=True,
        )
        for _attempt in range(3):
            token = secrets.token_urlsafe(32)
            created = await self._backend.set(
                self._key(token),
                payload,
                ex=self._ttl_seconds,
                nx=True,
            )
            if created:
                return IssuedControlTicket(
                    token=token,
                    expires_after_seconds=self._ttl_seconds,
                )
        raise RuntimeError("Unable to allocate a unique realtime control ticket")

    async def consume(self, token: str) -> ControlTicketClaims | None:
        if not token or len(token) < 32 or len(token) > 256:
            return None
        payload = await self._backend.getdel(self._key(token))
        if payload is None:
            return None
        try:
            decoded = json.loads(payload)
            if not isinstance(decoded, dict):
                return None
            return ControlTicketClaims(
                user_id=UUID(str(decoded["user_id"])),
                interview_session_id=UUID(str(decoded["interview_session_id"])),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _key(token: str) -> str:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{TICKET_KEY_PREFIX}{digest}"


async def get_control_ticket_store(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[ControlTicketStore]:
    redis: Redis = build_redis(settings)
    try:
        yield ControlTicketStore(
            cast(AtomicTicketBackend, redis),
            ttl_seconds=settings.realtime_control_ticket_ttl_seconds,
        )
    finally:
        await redis.aclose()

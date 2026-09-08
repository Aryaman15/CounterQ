from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.principal import ExternalPrincipal
from app.auth.repository import UserRepository


class AccountUnavailable(PermissionError):
    """The external identity maps to an account that cannot use product resources."""


class UserIdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)

    async def resolve_or_create_current_user(self, principal: ExternalPrincipal) -> User:
        user = await self._users.resolve_or_create(principal)
        if user.status != "ACTIVE" or user.deleted_at is not None:
            raise AccountUnavailable("CounterQ account is not active")
        return user

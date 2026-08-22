from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        external_auth_provider: str,
        external_auth_subject: str,
        status: str = "ACTIVE",
    ) -> User:
        user = User(
            external_auth_provider=external_auth_provider,
            external_auth_subject=external_auth_subject,
            status=status,
        )
        self._session.add(user)
        await self._session.flush()
        return user

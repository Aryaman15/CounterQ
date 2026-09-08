from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import CandidateProfile, User
from app.auth.principal import ExternalPrincipal


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

    async def resolve_or_create(self, principal: ExternalPrincipal) -> User:
        await self._session.execute(
            insert(User)
            .values(
                external_auth_provider=principal.provider,
                external_auth_subject=principal.subject,
                status="ACTIVE",
            )
            .on_conflict_do_nothing(
                index_elements=["external_auth_provider", "external_auth_subject"]
            )
        )
        user = await self._session.scalar(
            select(User).where(
                User.external_auth_provider == principal.provider,
                User.external_auth_subject == principal.subject,
            )
        )
        if user is None:
            raise RuntimeError("CounterQ user resolution did not produce a user")
        return user


class CandidateProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID) -> CandidateProfile | None:
        return await self._session.get(CandidateProfile, user_id)

    async def upsert(
        self,
        *,
        user_id: UUID,
        display_name: str | None,
        preferred_language: str,
        default_interview_mode: str,
        interview_level: str,
        target_role: str | None,
        timezone: str | None,
    ) -> CandidateProfile:
        statement = (
            insert(CandidateProfile)
            .values(
                user_id=user_id,
                display_name=display_name,
                preferred_language=preferred_language,
                default_interview_mode=default_interview_mode,
                interview_level=interview_level,
                target_role=target_role,
                timezone=timezone,
                profile_version=1,
            )
            .on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "display_name": display_name,
                    "preferred_language": preferred_language,
                    "default_interview_mode": default_interview_mode,
                    "interview_level": interview_level,
                    "target_role": target_role,
                    "timezone": timezone,
                    "profile_version": CandidateProfile.profile_version + 1,
                    "updated_at": text("CURRENT_TIMESTAMP"),
                },
            )
            .returning(CandidateProfile)
        )
        return (await self._session.execute(statement)).scalar_one()

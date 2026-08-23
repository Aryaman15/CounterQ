from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.models import AIInvocation


class AIInvocationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        user_id: UUID,
        interview_session_id: UUID,
        ai_policy_version_id: UUID,
        provider: str = "test-provider",
        model: str = "test-model",
        capability: str = "CHEAP_ANALYSIS",
        purpose: str = "CLAIM_EXTRACTION",
        status: str = "SUCCEEDED",
        started_at: datetime,
        completed_at: datetime | None = None,
        estimated_cost: Decimal | None = None,
    ) -> AIInvocation:
        invocation = AIInvocation(
            user_id=user_id,
            interview_session_id=interview_session_id,
            provider=provider,
            model=model,
            capability=capability,
            purpose=purpose,
            ai_policy_version_id=ai_policy_version_id,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            estimated_cost=estimated_cost,
            currency="USD" if estimated_cost is not None else None,
        )
        self._session.add(invocation)
        await self._session.flush()
        return invocation

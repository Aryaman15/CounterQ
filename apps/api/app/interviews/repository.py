from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import InterviewConfiguration, InterviewSession, SessionBudget
from app.observation.models import InterviewEvent
from app.problems.models import InterviewPackVersion


class InterviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_configuration(
        self,
        *,
        mode: str,
        level: str,
        language: str,
        configured_duration_seconds: int,
        problem_source: str,
    ) -> InterviewConfiguration:
        configuration = InterviewConfiguration(
            mode=mode,
            level=level,
            language=language,
            configured_duration_seconds=configured_duration_seconds,
            problem_source=problem_source,
        )
        self._session.add(configuration)
        await self._session.flush()
        return configuration

    async def add_session(
        self,
        *,
        user_id: UUID,
        configuration_id: UUID,
        problem_version_id: UUID,
        interview_pack_version_id: UUID,
        current_stage: str,
        state_version: int,
        status: str,
        started_at: datetime,
        deadline_at: datetime,
    ) -> InterviewSession:
        pack = await self._session.get(InterviewPackVersion, interview_pack_version_id)
        if pack is None or pack.problem_version_id != problem_version_id:
            raise ValueError("Interview Pack must belong to the session ProblemVersion")
        interview_session = InterviewSession(
            user_id=user_id,
            interview_configuration_id=configuration_id,
            problem_version_id=problem_version_id,
            interview_pack_version_id=interview_pack_version_id,
            current_stage=current_stage,
            state_version=state_version,
            status=status,
            started_at=started_at,
            deadline_at=deadline_at,
        )
        self._session.add(interview_session)
        await self._session.flush()
        return interview_session

    async def add_budget(
        self,
        *,
        session_id: UUID,
        max_duration_seconds: int,
        max_probes: int,
        max_deep_reasoning_calls: int,
        reserved_post_interview_deep_reasoning_calls: int,
        max_strong_reasoning_calls: int,
        max_vision_calls: int,
        soft_monetary_budget: Decimal,
        hard_monetary_budget: Decimal,
        realtime_reserved_budget: Decimal,
        max_assistance_interventions: int = 0,
        max_structural_hints: int = 0,
        max_direct_teaching_interventions: int = 0,
        max_guided_retries: int = 0,
        max_report_reasoning_calls: int = 4,
    ) -> SessionBudget:
        budget = SessionBudget(
            session_id=session_id,
            max_duration_seconds=max_duration_seconds,
            max_probes=max_probes,
            max_deep_reasoning_calls=max_deep_reasoning_calls,
            max_report_reasoning_calls=max_report_reasoning_calls,
            reserved_post_interview_deep_reasoning_calls=(
                reserved_post_interview_deep_reasoning_calls
            ),
            max_strong_reasoning_calls=max_strong_reasoning_calls,
            max_vision_calls=max_vision_calls,
            soft_monetary_budget=soft_monetary_budget,
            hard_monetary_budget=hard_monetary_budget,
            realtime_reserved_budget=realtime_reserved_budget,
            max_assistance_interventions=max_assistance_interventions,
            max_structural_hints=max_structural_hints,
            max_direct_teaching_interventions=max_direct_teaching_interventions,
            max_guided_retries=max_guided_retries,
        )
        self._session.add(budget)
        await self._session.flush()
        return budget

    async def add_event(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        event_type: str,
        source: str,
        occurred_at: datetime,
        received_at: datetime,
        server_sequence: int,
        interview_state_version: int,
        schema_version: str,
        idempotency_key: str | None = None,
        payload: dict[str, object] | None = None,
        provenance: dict[str, object] | None = None,
    ) -> InterviewEvent:
        event = InterviewEvent(
            interview_session_id=session_id,
            user_id=user_id,
            event_type=event_type,
            source=source,
            occurred_at=occurred_at,
            received_at=received_at,
            server_sequence=server_sequence,
            interview_state_version=interview_state_version,
            schema_version=schema_version,
            idempotency_key=idempotency_key,
            payload=payload or {},
            provenance=provenance or {},
        )
        self._session.add(event)
        await self._session.flush()
        return event

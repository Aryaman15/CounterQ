"""Deterministic CounterMap generation with claim-safe publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.countermap.models import CounterMapProjection
from app.countermap.projector import CounterMapProjector
from app.countermap.repository import CounterMapProjectionRepository
from app.countermap.schema import (
    COUNTERMAP_GENERATION_POLICY_VERSION,
    CounterMapGraph,
)
from app.countermap.source import CounterMapSourceBuilder
from app.countermap.validator import CounterMapValidationError, CounterMapValidator
from app.interviews.models import InterviewSession
from app.outbox.claims import OutboxWorkClaim
from app.outbox.models import OutboxEvent


class CounterMapGenerationError(RuntimeError):
    def __init__(self, category: str, safe_message: str) -> None:
        self.category = category
        self.safe_message = safe_message
        super().__init__(safe_message)


class CounterMapWorkOwnershipLost(CounterMapGenerationError):
    def __init__(self) -> None:
        super().__init__("OUTBOX_OWNERSHIP_LOST", "CounterMap work claim is no longer current")


@dataclass(frozen=True)
class CounterMapGenerationResult:
    projection_id: UUID
    projection_version: int
    created: bool
    source_identity: str
    semantic_identity: str


class CounterMapGenerationService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        projector: CounterMapProjector | None = None,
        validator: CounterMapValidator | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._projector = projector or CounterMapProjector()
        self._validator = validator or CounterMapValidator()

    async def generate(
        self,
        *,
        interview_session_id: UUID,
        generation_request_key: str,
        work_claim: OutboxWorkClaim | None = None,
    ) -> CounterMapGenerationResult:
        if work_claim is not None:
            async with self._sessionmaker() as session, session.begin():
                await _require_work_claim(
                    session,
                    work_claim=work_claim,
                    interview_session_id=interview_session_id,
                )
        async with self._sessionmaker() as read_session:
            bundle = await CounterMapSourceBuilder(read_session).build(interview_session_id)

        existing_result: CounterMapGenerationResult | None = None
        reuse_error: CounterMapGenerationError | None = None
        async with self._sessionmaker() as session, session.begin():
            if work_claim is not None:
                await _require_work_claim(
                    session,
                    work_claim=work_claim,
                    interview_session_id=interview_session_id,
                )
            projection, should_generate = await CounterMapProjectionRepository(
                session
            ).prepare_generation(
                session_id=interview_session_id,
                generation_request_key=generation_request_key,
                source_watermark=bundle.source_watermark,
                source_identity=bundle.source_identity,
            )
            projection_id = projection.id
            projection_version = projection.projection_version
            if not should_generate:
                if projection.status == "STALE":
                    reuse_error = CounterMapGenerationError(
                        "COUNTERMAP_PROVENANCE_MISMATCH",
                        "Existing CounterMap request key belongs to different canonical sources",
                    )
                elif projection.graph_json is None:
                    reuse_error = CounterMapGenerationError(
                        "COUNTERMAP_PROVENANCE_MISSING",
                        "Existing CounterMap is missing its graph",
                    )
                else:
                    existing_graph = CounterMapGraph.model_validate(projection.graph_json)
                    existing_result = CounterMapGenerationResult(
                        projection_id=projection.id,
                        projection_version=projection.projection_version,
                        created=False,
                        source_identity=projection.source_identity,
                        semantic_identity=existing_graph.semantic_identity(),
                    )

        if reuse_error is not None:
            raise reuse_error
        if existing_result is not None:
            return existing_result

        try:
            graph = self._projector.project(bundle)
            self._validator.validate(bundle=bundle, graph=graph)
        except CounterMapValidationError as exc:
            async with self._sessionmaker() as session, session.begin():
                if work_claim is not None:
                    await _require_work_claim(
                        session,
                        work_claim=work_claim,
                        interview_session_id=interview_session_id,
                    )
                await CounterMapProjectionRepository(session).mark_failed(
                    projection_id=projection_id,
                    category="COUNTERMAP_VALIDATION_FAILED",
                )
            raise CounterMapGenerationError(
                "COUNTERMAP_VALIDATION_FAILED",
                "CounterMap did not pass canonical source validation",
            ) from exc
        except Exception as exc:
            async with self._sessionmaker() as session, session.begin():
                if work_claim is not None:
                    await _require_work_claim(
                        session,
                        work_claim=work_claim,
                        interview_session_id=interview_session_id,
                    )
                await CounterMapProjectionRepository(session).mark_failed(
                    projection_id=projection_id,
                    category=type(exc).__name__,
                )
            raise CounterMapGenerationError(
                type(exc).__name__,
                "CounterMap generation could not be completed",
            ) from exc

        source_changed = False
        async with self._sessionmaker() as session, session.begin():
            if work_claim is not None:
                await _require_work_claim(
                    session,
                    work_claim=work_claim,
                    interview_session_id=interview_session_id,
                )
            locked_interview = await session.scalar(
                select(InterviewSession)
                .where(InterviewSession.id == interview_session_id)
                .with_for_update()
            )
            if locked_interview is None:
                raise CounterMapGenerationError("SESSION_NOT_FOUND", "Interview was not found")
            fresh_bundle = await CounterMapSourceBuilder(session).build(interview_session_id)
            repository = CounterMapProjectionRepository(session)
            if fresh_bundle.source_identity != bundle.source_identity:
                await repository.mark_stale(projection_id)
                source_changed = True
            else:
                self._validator.validate(bundle=fresh_bundle, graph=graph)
                pending = await session.get(CounterMapProjection, projection_id)
                if pending is None:
                    raise CounterMapGenerationError(
                        "COUNTERMAP_NOT_FOUND",
                        "Pending CounterMap projection disappeared",
                    )
                await repository.mark_ready(
                    projection=pending,
                    graph_json=graph.model_dump(mode="json"),
                    generated_at=datetime.now(UTC),
                )
        if source_changed:
            raise CounterMapGenerationError(
                "SOURCE_CHANGED",
                "Canonical CounterMap sources changed during generation",
            )
        return CounterMapGenerationResult(
            projection_id=projection_id,
            projection_version=projection_version,
            created=True,
            source_identity=bundle.source_identity,
            semantic_identity=graph.semantic_identity(),
        )


def initial_countermap_generation_key(session_id: UUID) -> str:
    return f"countermap:{session_id}:{COUNTERMAP_GENERATION_POLICY_VERSION}:initial"


async def _require_work_claim(
    session: AsyncSession,
    *,
    work_claim: OutboxWorkClaim,
    interview_session_id: UUID,
) -> OutboxEvent:
    event = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.id == work_claim.outbox_event_id).with_for_update()
    )
    if (
        event is None
        or event.interview_session_id != interview_session_id
        or event.event_type != "GENERATE_COUNTERMAP"
        or event.attempt_count != work_claim.attempt
        or event.status != "PROCESSING"
    ):
        raise CounterMapWorkOwnershipLost()
    return event

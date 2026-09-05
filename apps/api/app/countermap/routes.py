"""Candidate-safe CounterMap read/status and development inspection APIs."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.environment import development_spike_enabled
from app.config.settings import Settings, get_settings
from app.countermap.detail import (
    CandidateCounterMapNodeDetailResponse,
    CounterMapNodeDetailResolver,
    CounterMapNodeNotFound,
    assemble_candidate_detail,
    attach_development_source,
)
from app.countermap.development_fixtures import (
    development_source_code,
    load_development_countermap_fixtures,
)
from app.countermap.projector import CounterMapProjector
from app.countermap.repository import CounterMapProjectionRepository
from app.countermap.schema import COUNTERMAP_GENERATION_POLICY_VERSION, CounterMapGraph
from app.countermap.validator import CounterMapValidator
from app.db.session import get_session, get_sessionmaker
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.outbox.models import OutboxEvent
from app.outbox.repository import OutboxRepository
from app.problems.models import ProblemVersion

router = APIRouter(prefix="/api/countermap", tags=["countermap"])


class CounterMapSessionMetadata(BaseModel):
    problem_title: str
    mode: Literal["COACH", "SIMULATION"]
    language: str
    completed_at: datetime
    duration_seconds: int


class CandidateCounterMapResponse(BaseModel):
    status: Literal["NOT_AVAILABLE", "BUILDING", "READY", "FAILED", "STALE"]
    session: CounterMapSessionMetadata
    projection_id: UUID | None
    projection_version: int | None
    schema_version: str | None
    generated_at: datetime | None
    graph: CounterMapGraph | None
    message: str


class DevelopmentCounterMapRegenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)


class DevelopmentCounterMapRegenerationResponse(BaseModel):
    outbox_event_id: UUID
    created: bool
    status: str


class DevelopmentCounterMapInspection(BaseModel):
    interview_session_id: UUID
    projection_status: str
    projection_id: UUID | None
    projection_version: int | None
    schema_version: str | None
    generation_policy_version: str | None
    source_watermark: int | None
    generated_at: datetime | None
    node_count: int
    edge_count: int
    node_counts: dict[str, int]
    relationship_counts: dict[str, int]
    validation_outcome: str
    outbox_generation_state: str
    last_failure_category: str | None
    outbox: list[dict[str, Any]]


class DevelopmentCounterMapFixtureResponse(BaseModel):
    fixture_id: str
    label: str
    description: str
    graph: CounterMapGraph


@router.get(
    "/development/fixtures",
    response_model=list[DevelopmentCounterMapFixtureResponse],
)
async def development_countermap_fixtures(
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[DevelopmentCounterMapFixtureResponse]:
    _require_development(settings)
    projector = CounterMapProjector()
    validator = CounterMapValidator()
    result: list[DevelopmentCounterMapFixtureResponse] = []
    for fixture in load_development_countermap_fixtures():
        graph = projector.project(fixture.bundle)
        validator.validate(bundle=fixture.bundle, graph=graph)
        result.append(
            DevelopmentCounterMapFixtureResponse(
                fixture_id=fixture.fixture_id,
                label=fixture.label,
                description=fixture.description,
                graph=graph,
            )
        )
    return result


@router.get(
    "/sessions/{interview_session_id}",
    response_model=CandidateCounterMapResponse,
)
async def countermap_status(
    interview_session_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateCounterMapResponse:
    interview, configuration, problem = await _session_facts(session, interview_session_id)
    if interview.status != "COMPLETED" or interview.completed_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="CounterMap is available only after interview completion",
        )
    metadata = CounterMapSessionMetadata(
        problem_title=problem.title,
        mode=configuration.mode,
        language=configuration.language,
        completed_at=interview.completed_at,
        duration_seconds=max(
            0, int((interview.completed_at - interview.started_at).total_seconds())
        ),
    )
    repository = CounterMapProjectionRepository(session)
    ready = await repository.current_ready(interview.id)
    if ready is not None and ready.graph_json is not None:
        try:
            graph = CounterMapGraph.model_validate(ready.graph_json)
        except ValidationError:
            return _response(
                metadata,
                status_value="FAILED",
                projection=ready,
                message=_failure_message(),
            )
        return CandidateCounterMapResponse(
            status="READY",
            session=metadata,
            projection_id=ready.id,
            projection_version=ready.projection_version,
            schema_version=ready.schema_version,
            generated_at=ready.generated_at,
            graph=graph,
            message="Your evidence-backed reasoning map is ready.",
        )
    latest = await repository.latest(interview.id)
    failed_event = await session.scalar(
        select(OutboxEvent.id).where(
            OutboxEvent.interview_session_id == interview.id,
            OutboxEvent.event_type == "GENERATE_COUNTERMAP",
            OutboxEvent.status == "FAILED",
        )
    )
    if (latest and latest.status == "FAILED") or failed_event is not None:
        return _response(
            metadata,
            status_value="FAILED",
            projection=latest,
            message=_failure_message(),
        )
    if latest and latest.status == "STALE":
        return _response(
            metadata,
            status_value="STALE",
            projection=latest,
            message="Your reasoning map is being rebuilt from updated interview evidence.",
        )
    pending = await session.scalar(
        select(OutboxEvent.id).where(
            OutboxEvent.interview_session_id == interview.id,
            OutboxEvent.event_type.in_(("FINALIZE_SESSION_EVIDENCE", "GENERATE_COUNTERMAP")),
            OutboxEvent.status.in_(("PENDING", "PUBLISHED", "PROCESSING", "RETRY")),
        )
    )
    if pending is not None or (latest and latest.status == "BUILDING"):
        return _response(
            metadata,
            status_value="BUILDING",
            projection=latest,
            message="CounterQ is tracing the evidence-backed story of your interview.",
        )
    return _response(
        metadata,
        status_value="NOT_AVAILABLE",
        projection=latest,
        message="A reasoning map has not been prepared for this interview yet.",
    )


@router.get(
    "/sessions/{interview_session_id}/nodes/{node_id}",
    response_model=CandidateCounterMapNodeDetailResponse,
)
async def countermap_node_detail(
    interview_session_id: UUID,
    node_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateCounterMapNodeDetailResponse:
    interview, _configuration, _problem = await _session_facts(session, interview_session_id)
    if interview.status != "COMPLETED" or interview.completed_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="CounterMap is available only after interview completion",
        )
    projection = await CounterMapProjectionRepository(session).current_ready(interview.id)
    if projection is None or projection.graph_json is None:
        raise HTTPException(status_code=409, detail="CounterMap detail is not ready")
    try:
        graph = CounterMapGraph.model_validate(projection.graph_json)
        return await CounterMapNodeDetailResolver(session).resolve(
            session_id=interview.id,
            projection=projection,
            graph=graph,
            node_id=node_id,
        )
    except (ValidationError, CounterMapNodeNotFound) as exc:
        raise HTTPException(status_code=404, detail="CounterMap node was not found") from exc


@router.post(
    "/development/sessions/{interview_session_id}/regenerate",
    response_model=DevelopmentCounterMapRegenerationResponse,
)
async def regenerate_countermap(
    interview_session_id: UUID,
    request: DevelopmentCounterMapRegenerationRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DevelopmentCounterMapRegenerationResponse:
    _require_development(settings)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        interview = await session.scalar(
            select(InterviewSession)
            .where(InterviewSession.id == interview_session_id)
            .with_for_update()
        )
        if interview is None:
            raise HTTPException(status_code=404, detail="Interview session was not found")
        if interview.status != "COMPLETED":
            raise HTTPException(status_code=409, detail="Interview is not completed")
        generation_key = (
            f"countermap:{interview.id}:{COUNTERMAP_GENERATION_POLICY_VERSION}:"
            f"regenerate:{request.idempotency_key}"
        )
        event, created = await OutboxRepository(session).enqueue(
            aggregate_type="InterviewSession",
            aggregate_id=interview.id,
            interview_session_id=interview.id,
            event_type="GENERATE_COUNTERMAP",
            payload={
                "interview_session_id": str(interview.id),
                "generation_request_key": generation_key,
                "generation_policy": COUNTERMAP_GENERATION_POLICY_VERSION,
            },
            deduplication_key=generation_key,
            available_at=datetime.now(UTC),
            source_watermark=interview.last_server_sequence,
        )
        return DevelopmentCounterMapRegenerationResponse(
            outbox_event_id=event.id,
            created=created,
            status=event.status,
        )


@router.get(
    "/development/sessions/{interview_session_id}/inspection",
    response_model=DevelopmentCounterMapInspection,
)
async def development_countermap_inspection(
    interview_session_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevelopmentCounterMapInspection:
    _require_development(settings)
    interview = await session.get(InterviewSession, interview_session_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="Interview session was not found")
    outbox = list(
        await session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.interview_session_id == interview.id,
                OutboxEvent.event_type.in_(("FINALIZE_SESSION_EVIDENCE", "GENERATE_COUNTERMAP")),
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
        )
    )
    latest = await CounterMapProjectionRepository(session).latest(interview.id)
    graph: CounterMapGraph | None = None
    validation_outcome = "NOT_RUN"
    if latest and latest.graph_json:
        try:
            graph = CounterMapGraph.model_validate(latest.graph_json)
        except ValidationError:
            validation_outcome = "FAILED"
        else:
            validation_outcome = "PASSED" if latest.status == "READY" else "PENDING"
    elif latest:
        validation_outcome = "FAILED" if latest.status == "FAILED" else "PENDING"
    node_counts = Counter(item.node_type for item in graph.nodes) if graph else Counter()
    relationship_counts = Counter(item.relationship for item in graph.edges) if graph else Counter()
    generation = next(
        (item.status for item in reversed(outbox) if item.event_type == "GENERATE_COUNTERMAP"),
        "NOT_STARTED",
    )
    return DevelopmentCounterMapInspection(
        interview_session_id=interview.id,
        projection_status=latest.status if latest else "NOT_STARTED",
        projection_id=latest.id if latest else None,
        projection_version=latest.projection_version if latest else None,
        schema_version=latest.schema_version if latest else None,
        generation_policy_version=latest.generation_policy_version if latest else None,
        source_watermark=latest.source_watermark if latest else None,
        generated_at=latest.generated_at if latest else None,
        node_count=len(graph.nodes) if graph else 0,
        edge_count=len(graph.edges) if graph else 0,
        node_counts=dict(sorted(node_counts.items())),
        relationship_counts=dict(sorted(relationship_counts.items())),
        validation_outcome=validation_outcome,
        outbox_generation_state=generation,
        last_failure_category=(
            latest.last_failure_category
            if latest and latest.last_failure_category
            else next((item.last_error for item in reversed(outbox) if item.last_error), None)
        ),
        outbox=[
            {
                "id": item.id,
                "event_type": item.event_type,
                "status": item.status,
                "attempt_count": item.attempt_count,
                "last_error": item.last_error,
            }
            for item in outbox
        ],
    )


@router.get(
    "/development/fixtures/{fixture_id}/nodes/{node_id}",
    response_model=CandidateCounterMapNodeDetailResponse,
)
async def development_countermap_node_detail(
    fixture_id: str,
    node_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CandidateCounterMapNodeDetailResponse:
    _require_development(settings)
    fixture = next(
        (item for item in load_development_countermap_fixtures() if item.fixture_id == fixture_id),
        None,
    )
    if fixture is None:
        raise HTTPException(status_code=404, detail="CounterMap fixture was not found")
    graph = CounterMapProjector().project(fixture.bundle)
    CounterMapValidator().validate(bundle=fixture.bundle, graph=graph)
    node = next((item for item in graph.nodes if item.node_id == node_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="CounterMap node was not found")
    try:
        detail = assemble_candidate_detail(node=node, bundle=fixture.bundle)
    except CounterMapNodeNotFound as exc:
        raise HTTPException(status_code=404, detail="CounterMap node was not found") from exc
    return attach_development_source(
        detail=detail,
        node=node,
        bundle=fixture.bundle,
        source_code_for_version=lambda version: development_source_code(fixture_id, version),
    )


def _response(
    metadata: CounterMapSessionMetadata,
    *,
    status_value: Literal["NOT_AVAILABLE", "BUILDING", "FAILED", "STALE"],
    projection: Any,
    message: str,
) -> CandidateCounterMapResponse:
    return CandidateCounterMapResponse(
        status=status_value,
        session=metadata,
        projection_id=projection.id if projection else None,
        projection_version=projection.projection_version if projection else None,
        schema_version=projection.schema_version if projection else None,
        generated_at=projection.generated_at if projection else None,
        graph=None,
        message=message,
    )


def _failure_message() -> str:
    return (
        "CounterMap is unavailable for this interview. "
        "Your report and interview evidence are still safe."
    )


async def _session_facts(
    session: AsyncSession,
    session_id: UUID,
) -> tuple[InterviewSession, InterviewConfiguration, ProblemVersion]:
    interview = await session.get(InterviewSession, session_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="Interview session was not found")
    configuration = await session.get(InterviewConfiguration, interview.interview_configuration_id)
    problem = await session.get(ProblemVersion, interview.problem_version_id)
    if configuration is None or problem is None:
        raise HTTPException(status_code=500, detail="Session metadata is unavailable")
    return interview, configuration, problem


def _require_development(settings: Settings) -> None:
    if not development_spike_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "CounterMap regeneration and inspection are development-only",
            },
        )

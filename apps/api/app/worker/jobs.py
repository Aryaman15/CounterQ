"""Synchronous RQ entrypoints wrapping CounterQ async application services."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.provider_factory import build_reasoning_provider
from app.config.settings import get_settings
from app.countermap.service import CounterMapGenerationService
from app.db.registry import register_orm_models
from app.db.session import build_engine
from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.outbox.consumer import PostSessionOutboxConsumer
from app.reports.service import SessionReportGenerationService


def consume_outbox_event(outbox_event_id: str, attempt: int) -> dict[str, str | None]:
    """RQ boundary; durable retry state remains owned by PostgreSQL."""

    register_orm_models()
    return asyncio.run(_consume(UUID(outbox_event_id), attempt))


async def _consume(outbox_event_id: UUID, attempt: int) -> dict[str, str | None]:
    settings = get_settings()
    engine = build_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    gateway = AIGateway(
        settings=settings,
        sessionmaker=sessionmaker,
        provider=build_reasoning_provider(settings),
    )
    consumer = PostSessionOutboxConsumer(
        sessionmaker=sessionmaker,
        evidence_coordinator=SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessionmaker,
            ai_gateway=gateway,
        ),
        report_service=SessionReportGenerationService(
            sessionmaker=sessionmaker,
            ai_gateway=gateway,
            reasoning_timeout_seconds=(settings.session_report_reasoning_timeout_seconds),
        ),
        countermap_service=CounterMapGenerationService(sessionmaker=sessionmaker),
        max_attempts=settings.outbox_max_attempts,
        processing_lease_seconds=settings.outbox_claim_lease_seconds,
    )
    try:
        result = await consumer.consume(outbox_event_id, attempt)
        return {
            "outbox_event_id": str(result.outbox_event_id),
            "status": result.status,
            "category": result.category,
        }
    finally:
        await engine.dispose()

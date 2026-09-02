from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai_gateway.routes import router as ai_gateway_router
from app.api.routes.health import router as health_router
from app.config.settings import get_settings
from app.core.logging import CorrelationIdMiddleware, configure_logging
from app.db.registry import register_orm_models
from app.evidence.routes import router as evidence_router
from app.examiner.routes import router as examiner_router
from app.execution.routes import router as execution_router
from app.problems.routes import router as curated_problem_router
from app.realtime.routes import router as realtime_router


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    register_orm_models()

    app = FastAPI(
        title="CounterQ API",
        version="0.0.0",
        summary="Stage 1 Core Interaction Spike API for CounterQ.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.local_web_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health_router)
    app.include_router(ai_gateway_router)
    app.include_router(examiner_router)
    app.include_router(execution_router)
    app.include_router(evidence_router)
    app.include_router(curated_problem_router)
    app.include_router(realtime_router)
    return app


app = create_app()

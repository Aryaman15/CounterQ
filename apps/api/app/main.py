from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai_gateway.routes import router as ai_gateway_router
from app.api.routes.health import router as health_router
from app.auth.routes import router as auth_router
from app.config.settings import get_settings
from app.core.logging import CorrelationIdMiddleware, configure_logging
from app.countermap.routes import router as countermap_router
from app.db.registry import register_orm_models
from app.evidence.routes import router as evidence_router
from app.examiner.routes import router as examiner_router
from app.execution.routes import router as execution_router
from app.interviews.routes import router as interviews_router
from app.mastery.routes import router as mastery_router
from app.problems.custom_routes import router as custom_problem_router
from app.problems.routes import router as curated_problem_router
from app.realtime.routes import router as realtime_router
from app.reports.routes import router as reports_router
from app.retests.routes import router as retests_router


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    register_orm_models()

    app = FastAPI(
        title="CounterQ API",
        version="0.0.0",
        summary="CounterQ application API.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_frontend_origin_values),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(ai_gateway_router)
    app.include_router(examiner_router)
    app.include_router(execution_router)
    app.include_router(interviews_router)
    app.include_router(evidence_router)
    app.include_router(curated_problem_router)
    app.include_router(custom_problem_router)
    app.include_router(realtime_router)
    app.include_router(reports_router)
    app.include_router(countermap_router)
    app.include_router(mastery_router)
    app.include_router(retests_router)
    return app


app = create_app()

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.config.settings import get_settings
from app.core.logging import CorrelationIdMiddleware, configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="CounterQ API",
        version="0.0.0",
        summary="Stage 0 infrastructure foundation for CounterQ.",
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health_router)
    return app


app = create_app()

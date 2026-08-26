from __future__ import annotations

from fastapi import FastAPI

from .api.health import router as health_router
from .api.jobs import router as jobs_router
from .core.config import get_settings
from .core.store import JobStore


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.service_name, version=settings.service_version)
    app.state.settings = settings
    app.state.store = JobStore(settings)
    app.include_router(health_router)
    app.include_router(jobs_router)
    return app


app = create_app()

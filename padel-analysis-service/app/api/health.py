from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..core.config import Settings
from ..core.security import require_secret
from ..core.store import JobStore

router = APIRouter(tags=["health"])


def _get_store(request: Request) -> JobStore:
    return request.app.state.store


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
def ready(request: Request, _auth: None = Depends(require_secret)) -> dict[str, str]:
    settings = _get_settings(request)
    store = _get_store(request)
    if not settings.api_secret:
        return {"status": "not_ready", "reason": "missing_api_secret"}
    store.advance_due_jobs()
    return {"status": "ready"}

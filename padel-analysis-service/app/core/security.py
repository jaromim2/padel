from __future__ import annotations

from fastapi import Header, HTTPException, status

from .config import get_settings

SECRET_HEADER = "X-Padel-API-Secret"


def require_secret(x_padel_api_secret: str | None = Header(default=None, alias=SECRET_HEADER)) -> None:
    settings = get_settings()
    if not settings.api_secret or x_padel_api_secret != settings.api_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API secret.",
        )

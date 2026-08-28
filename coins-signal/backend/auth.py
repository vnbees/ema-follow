from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from config import settings


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    expected = (settings.signal_api_key or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SIGNAL_API_KEY not configured",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key.strip(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

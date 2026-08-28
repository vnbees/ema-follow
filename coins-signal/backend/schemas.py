from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SignalPublic(BaseModel):
    id: int
    external_id: str
    symbol: str
    side: str
    trend: str | None = None
    status: str
    entry: float
    tp: float | None = None
    close_price: float | None = None
    equity_pct: float
    pnl_pct: float | None = None
    close_reason: str | None = None
    opened_at: datetime
    closed_at: datetime | None = None

    model_config = {"from_attributes": True}


class SignalUpsertIn(BaseModel):
    external_id: str = Field(..., min_length=1, max_length=64)
    symbol: str = Field(..., min_length=1, max_length=32)
    side: Literal["long", "short"]
    trend: str | None = None
    entry: float
    tp: float | None = None
    equity_pct: float = Field(..., gt=0, le=100)
    opened_at: datetime


class SignalCloseIn(BaseModel):
    close_px: float
    pnl_pct: float | None = None
    close_reason: str | None = None
    closed_at: datetime | None = None


class SignalListOut(BaseModel):
    status: str
    page: int
    page_size: int
    total: int
    pages: int
    open_count: int
    closed_count: int
    signals: list[SignalPublic]

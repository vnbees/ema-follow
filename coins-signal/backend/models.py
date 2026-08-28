from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_status_opened_at", "status", "opened_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # long | short
    trend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open | closed
    entry_px: Mapped[float] = mapped_column(Float, nullable=False)
    tp_band: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity_pct: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

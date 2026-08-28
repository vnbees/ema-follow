from __future__ import annotations

from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_api_key
from database import get_db
from models import Signal
from schemas import SignalCloseIn, SignalListOut, SignalPublic, SignalUpsertIn

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])


def _to_public(row: Signal) -> SignalPublic:
    return SignalPublic(
        id=row.id,
        external_id=row.external_id,
        symbol=row.symbol,
        side=row.side,
        trend=row.trend,
        status=row.status,
        entry=row.entry_px,
        tp=row.tp_band,
        close_price=row.close_px,
        equity_pct=row.equity_pct,
        pnl_pct=row.pnl_pct,
        close_reason=row.close_reason,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
    )


@router.get("", response_model=SignalListOut)
async def list_signals(
    status_filter: str = Query("open", alias="status", pattern="^(open|closed|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> SignalListOut:
    open_count = int(
        (
            await db.execute(
                select(func.count()).select_from(Signal).where(Signal.status == "open")
            )
        ).scalar_one()
    )
    closed_count = int(
        (
            await db.execute(
                select(func.count()).select_from(Signal).where(Signal.status == "closed")
            )
        ).scalar_one()
    )

    query = select(Signal)
    count_query = select(func.count()).select_from(Signal)
    if status_filter != "all":
        query = query.where(Signal.status == status_filter)
        count_query = count_query.where(Signal.status == status_filter)

    total = int((await db.execute(count_query)).scalar_one())
    pages = max(1, ceil(total / page_size)) if total else 1
    order_col = Signal.closed_at.desc().nullslast() if status_filter == "closed" else Signal.opened_at.desc()
    rows = (
        await db.execute(
            query.order_by(order_col).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    return SignalListOut(
        status=status_filter,
        page=page,
        page_size=page_size,
        total=total,
        pages=pages,
        open_count=open_count,
        closed_count=closed_count,
        signals=[_to_public(r) for r in rows],
    )


@router.post("/reset", dependencies=[Depends(require_api_key)])
async def reset_signals(db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    """Delete all signals (open + closed). Requires X-API-Key."""
    result = await db.execute(delete(Signal))
    await db.commit()
    return {"deleted": int(result.rowcount or 0)}


@router.post("/upsert", response_model=SignalPublic, dependencies=[Depends(require_api_key)])
async def upsert_signal(
    body: SignalUpsertIn,
    db: AsyncSession = Depends(get_db),
) -> SignalPublic:
    external_id = body.external_id.strip()
    symbol = body.symbol.strip().upper()
    side = body.side.lower()

    existing = (
        await db.execute(select(Signal).where(Signal.external_id == external_id))
    ).scalar_one_or_none()

    if existing is None:
        row = Signal(
            external_id=external_id,
            symbol=symbol,
            side=side,
            trend=body.trend,
            status="open",
            entry_px=body.entry,
            tp_band=body.tp,
            equity_pct=body.equity_pct,
            opened_at=body.opened_at,
        )
        db.add(row)
    else:
        if existing.status == "closed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Signal already closed",
            )
        row = existing
        row.symbol = symbol
        row.side = side
        row.trend = body.trend
        row.status = "open"
        row.entry_px = body.entry
        row.tp_band = body.tp
        row.equity_pct = body.equity_pct
        row.opened_at = body.opened_at
        row.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(row)
    return _to_public(row)


@router.post(
    "/{external_id}/close",
    response_model=SignalPublic,
    dependencies=[Depends(require_api_key)],
)
async def close_signal(
    external_id: str,
    body: SignalCloseIn,
    db: AsyncSession = Depends(get_db),
) -> SignalPublic:
    row = (
        await db.execute(select(Signal).where(Signal.external_id == external_id.strip()))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")

    closed_at = body.closed_at or datetime.now(timezone.utc)
    row.status = "closed"
    row.close_px = body.close_px
    row.pnl_pct = body.pnl_pct
    row.close_reason = body.close_reason
    row.closed_at = closed_at
    row.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(row)
    return _to_public(row)

"""Publish sanitized Donchian signals to coins-signal API. Fail-soft — never breaks trading."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

import requests

from src.config import SIGNAL_API_KEY, SIGNAL_API_URL
from src.donchian.config import MARGIN_PCT


def signal_publish_configured() -> bool:
    return bool(SIGNAL_API_URL and SIGNAL_API_KEY)


def _utc_iso(value: str | datetime | None = None) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def equity_pct_from_size_mult(size_mult: float | None) -> float:
    mult = float(size_mult) if size_mult is not None else 1.0
    mult = min(2.0, max(0.5, mult))
    return float(MARGIN_PCT) * mult * 100.0


def pnl_pct_on_margin(pnl_usdt: float, margin_usdt: float | None) -> float | None:
    if margin_usdt is None:
        return None
    margin = float(margin_usdt)
    if margin <= 0:
        return None
    return (float(pnl_usdt) / margin) * 100.0


def _post(path: str, payload: dict[str, Any]) -> None:
    url = f"{SIGNAL_API_URL.rstrip('/')}{path}"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": SIGNAL_API_KEY,
    }
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if not response.ok:
        raise RuntimeError(
            f"signal publish HTTP {response.status_code}: {response.text[:200]}"
        )


def _run_bg(fn, *args, **kwargs) -> None:
    def _target() -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logging.warning("signal_publish failed: %s", exc)

    threading.Thread(target=_target, daemon=True, name="signal-publish").start()


def publish_open(
    *,
    lot_id: int,
    symbol: str,
    side: str,
    trend: str | None,
    entry: float,
    tp: float | None,
    size_mult: float | None,
    opened_at: str | None = None,
) -> None:
    if not signal_publish_configured():
        return

    payload = {
        "external_id": str(lot_id),
        "symbol": str(symbol).upper(),
        "side": str(side).lower(),
        "trend": trend,
        "entry": float(entry),
        "tp": float(tp) if tp is not None else None,
        "equity_pct": equity_pct_from_size_mult(size_mult),
        "opened_at": _utc_iso(opened_at),
    }
    _run_bg(_post, "/api/v1/signals/upsert", payload)


def publish_close(
    *,
    lot_id: int,
    close_px: float,
    pnl_usdt: float,
    margin_usdt: float | None,
    close_reason: str | None,
    closed_at: str | None = None,
) -> None:
    if not signal_publish_configured():
        return

    payload = {
        "close_px": float(close_px),
        "pnl_pct": pnl_pct_on_margin(pnl_usdt, margin_usdt),
        "close_reason": close_reason,
        "closed_at": _utc_iso(closed_at),
    }
    _run_bg(_post, f"/api/v1/signals/{lot_id}/close", payload)

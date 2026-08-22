"""Daily futures → spot skim transfer (07:00 +07) + risk warnings."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src import database as db
from src.config import (
    EXCHANGE,
    MARGIN_COIN,
    SPOT_TRANSFER_DAY_CAP_PCT,
    SPOT_TRANSFER_DD_PAUSE_PCT,
    SPOT_TRANSFER_ENABLED,
    SPOT_TRANSFER_EXECUTE_HHMM,
    SPOT_TRANSFER_MODE,
    SPOT_TRANSFER_PCT,
    SPOT_TRANSFER_SKIM,
    SPOT_WARN_COOLDOWN_SEC,
    SPOT_WARN_DD_PCT,
    SPOT_WARN_MAINT_PCT,
)
from src.exchange import (
    ExchangeClientError,
    fetch_futures_balance,
    fetch_futures_transfers,
    fetch_spot_balance,
    has_credentials,
    transfer_futures_to_spot,
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_MIN_TRANSFER_USDT = 0.01
_last_warn_at: dict[str, float] = {}


@dataclass(frozen=True)
class SkimDecision:
    amount: float
    reason: str
    day_pnl: float
    dd_pct: float
    sod: float
    equity: float
    peak: float
    available: float


def _parse_hhmm(raw: str, fallback: tuple[int, int]) -> tuple[int, int]:
    text = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(text) == 3:
        text = f"0{text}"
    if len(text) != 4:
        return fallback
    hour, minute = int(text[:2]), int(text[2:])
    if hour > 23 or minute > 59:
        return fallback
    return hour, minute


def _vn_now() -> datetime:
    return datetime.now(VN_TZ)


def _vn_date_str(now: datetime | None = None) -> str:
    return (now or _vn_now()).strftime("%Y-%m-%d")


def _minutes_of_day(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _hhmm_to_minutes(raw: str, fallback: tuple[int, int]) -> int:
    hour, minute = _parse_hhmm(raw, fallback)
    return hour * 60 + minute


def is_enabled() -> bool:
    return db.is_spot_transfer_enabled(SPOT_TRANSFER_ENABLED)


def set_enabled(enabled: bool) -> None:
    db.set_spot_transfer_enabled(enabled)


def note_equity_peak(equity: float) -> float:
    """Update high-water peak; return current peak."""
    if equity <= 0:
        peak = db.get_equity_peak()
        return peak if peak is not None else 0.0
    peak = db.get_equity_peak()
    if peak is None or equity > peak:
        db.set_equity_peak(equity)
        return equity
    return peak


def _bot_transfer_filters() -> tuple[set[str], set[tuple[str, float]]]:
    """(tranIds, (vn_date, amount)) of the bot's own successful transfers."""
    tran_ids: set[str] = set()
    date_amounts: set[tuple[str, float]] = set()
    for row in db.get_spot_transfers(limit=200):
        if str(row["status"]) != "success":
            continue
        tran_id = str(row["tran_id"] or "").strip()
        if tran_id:
            tran_ids.add(tran_id)
        date_amounts.add((str(row["transfer_date"]), round(float(row["amount"]), 2)))
    return tran_ids, date_amounts


def apply_manual_net_to_markers(net_manual: float) -> None:
    """Adjust SOD + peak by net manual TRANSFER (deposit +, withdraw −)."""
    if abs(net_manual) < 0.01:
        return
    sod = db.get_spot_sod_equity()
    if sod is not None:
        db.set_spot_sod_equity(sod + net_manual)
    peak = db.get_equity_peak()
    if peak is not None:
        db.set_equity_peak(max(0.0, peak + net_manual))
    logging.info(
        "  Spot markers adjusted %+.2f for manual transfer(s): sod %.2f -> %.2f, peak %.2f -> %.2f",
        net_manual,
        sod if sod is not None else float("nan"),
        (sod + net_manual) if sod is not None else float("nan"),
        peak if peak is not None else float("nan"),
        (peak + net_manual) if peak is not None else float("nan"),
    )


def sync_manual_transfers() -> bool:
    """Fold manual futures deposits/withdrawals into SOD + peak.

    Excludes the bot's own successful spot_transfers (by tranId, fallback date+amount).
    Returns False when income API failed — callers must not skim with unsynced markers.
    No-op / True on non-Binance (cannot fetch TRANSFER income).
    """
    if EXCHANGE != "binance":
        return True

    now_ms = int(time.time() * 1000)
    synced_at = db.get_spot_manual_synced_at_ms()
    if synced_at is None:
        # First run: don't replay history — start tracking from now.
        db.set_spot_manual_synced_at_ms(now_ms)
        logging.info("  Spot manual-transfer sync cursor initialized")
        return True
    if now_ms - synced_at < 1000:
        return True

    try:
        rows = fetch_futures_transfers(synced_at + 1, end_ms=now_ms)
    except ExchangeClientError as exc:
        logging.warning("  Spot manual-transfer sync failed — income API: %s", exc)
        return False

    bot_tran_ids, bot_date_amounts = _bot_transfer_filters()
    net_manual = 0.0
    for row in rows:
        if str(row.get("asset", "")).upper() != MARGIN_COIN:
            continue
        tran_id = str(row.get("tranId", "") or "")
        if tran_id and tran_id in bot_tran_ids:
            continue
        income = float(row.get("income") or 0)
        row_date = datetime.fromtimestamp(
            int(row.get("time") or 0) / 1000, tz=timezone.utc
        ).astimezone(VN_TZ).strftime("%Y-%m-%d")
        # Fallback: bot withdrawal whose income tranId ≠ sapi tranId
        if income < 0 and (row_date, round(-income, 2)) in bot_date_amounts:
            continue
        net_manual += income

    apply_manual_net_to_markers(net_manual)
    db.set_spot_manual_synced_at_ms(now_ms)
    return True


def decide_skim_withdraw(
    *,
    sod: float,
    equity: float,
    peak: float,
    available: float,
    skim: float = SPOT_TRANSFER_SKIM,
    day_cap_pct: float = SPOT_TRANSFER_DAY_CAP_PCT,
    dd_pause: float = SPOT_TRANSFER_DD_PAUSE_PCT,
    min_usdt: float = _MIN_TRANSFER_USDT,
) -> SkimDecision:
    """Pure decision for skim mode (no I/O)."""
    day_pnl = equity - sod
    peak_use = max(peak, equity, 1e-12)
    dd_pct = max(0.0, (peak_use - equity) / peak_use) if peak_use > 0 else 0.0
    base = SkimDecision(
        amount=0.0,
        reason="ok",
        day_pnl=day_pnl,
        dd_pct=dd_pct,
        sod=sod,
        equity=equity,
        peak=peak_use,
        available=available,
    )
    if day_pnl <= 0:
        return SkimDecision(**{**base.__dict__, "reason": "no_profit"})
    if dd_pct >= dd_pause:
        return SkimDecision(**{**base.__dict__, "reason": "dd_pause"})
    target = min(day_pnl * skim, sod * day_cap_pct)
    target = math.floor(max(0.0, target) * 100) / 100.0
    if target < min_usdt:
        return SkimDecision(**{**base.__dict__, "amount": target, "reason": "below_min"})
    take = min(target, max(available, 0.0))
    take = math.floor(take * 100) / 100.0
    if take < min_usdt:
        return SkimDecision(**{**base.__dict__, "amount": take, "reason": "no_cash"})
    return SkimDecision(**{**base.__dict__, "amount": take, "reason": "transfer"})


def _reason_label(reason: str, decision: SkimDecision) -> str:
    labels = {
        "transfer": (
            f"rút skim: day_pnl={decision.day_pnl:+.2f} × {SPOT_TRANSFER_SKIM:.0%} "
            f"cap={SPOT_TRANSFER_DAY_CAP_PCT:.2%}×SOD → {decision.amount:.2f}"
        ),
        "no_profit": f"không rút — day_pnl={decision.day_pnl:+.2f} ≤ 0",
        "dd_pause": (
            f"không rút — DD từ peak {decision.dd_pct*100:.1f}% "
            f"≥ {SPOT_TRANSFER_DD_PAUSE_PCT*100:.0f}%"
        ),
        "no_cash": (
            f"không rút — available={decision.available:.2f} < min "
            f"(target day_pnl skim)"
        ),
        "below_min": f"không rút — số tính được {decision.amount:.4f} < {_MIN_TRANSFER_USDT}",
        "first_marker": "mốc đầu — lấy equity hiện tại làm SOD, chưa rút",
        "disabled": "spot transfer tắt",
        "sync_failed": "không rút — sync TRANSFER thủ công thất bại",
        "pct": f"legacy pct {SPOT_TRANSFER_PCT}% equity",
    }
    return labels.get(reason, reason)


def _notify_decision(transfer_date: str, status: str, decision: SkimDecision, detail: str) -> None:
    try:
        from src.notify import notify_spot_transfer

        notify_spot_transfer(
            transfer_date=transfer_date,
            status=status,
            amount=decision.amount,
            detail=detail,
            day_pnl=decision.day_pnl,
            dd_pct=decision.dd_pct,
            equity=decision.equity,
            peak=decision.peak,
        )
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord spot-transfer notify failed: %s", exc)


def _execute_skim(ref_symbol: str, transfer_date: str) -> None:
    balance = fetch_futures_balance(ref_symbol)
    equity = float(balance.account_equity or 0)
    available = float(balance.available or 0)
    peak = note_equity_peak(equity)
    sod = db.get_spot_sod_equity()

    if sod is None:
        db.set_spot_sod_equity(equity, day=transfer_date)
        decision = SkimDecision(
            amount=0.0,
            reason="first_marker",
            day_pnl=0.0,
            dd_pct=0.0,
            sod=equity,
            equity=equity,
            peak=peak,
            available=available,
        )
        detail = _reason_label("first_marker", decision)
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=0.0,
            status="skipped",
            available_before=available,
            sod_equity=equity,
            eod_equity=equity,
            day_pnl=0.0,
            dd_pct=0.0,
            peak_equity=peak,
            reason="first_marker",
            error=detail,
        )
        logging.info("  Spot transfer %s — %s (equity=%.2f)", transfer_date, detail, equity)
        _notify_decision(transfer_date, "skipped", decision, detail)
        return

    decision = decide_skim_withdraw(
        sod=sod, equity=equity, peak=peak, available=available
    )
    detail = _reason_label(decision.reason, decision)

    if decision.reason != "transfer":
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=decision.amount,
            status="skipped",
            available_before=available,
            sod_equity=sod,
            eod_equity=equity,
            day_pnl=decision.day_pnl,
            dd_pct=decision.dd_pct * 100,
            peak_equity=peak,
            reason=decision.reason,
            error=detail,
        )
        # Next period SOD = equity now (no transfer)
        db.set_spot_sod_equity(equity, day=transfer_date)
        logging.info("  Spot transfer %s — %s", transfer_date, detail)
        _notify_decision(transfer_date, "skipped", decision, detail)
        return

    amount = decision.amount
    try:
        result = transfer_futures_to_spot(MARGIN_COIN, amount)
        spot_after: float | None
        try:
            spot_after = fetch_spot_balance(MARGIN_COIN)
        except ExchangeClientError as exc:
            logging.warning("  Spot balance after transfer failed: %s", exc)
            spot_after = None
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=amount,
            status="success",
            tran_id=str(result.get("tranId") or "") or None,
            available_before=available,
            spot_after=spot_after,
            sod_equity=sod,
            eod_equity=equity,
            day_pnl=decision.day_pnl,
            dd_pct=decision.dd_pct * 100,
            peak_equity=peak,
            reason="transfer",
            error=detail,
        )
        # SOD next = equity after withdrawal (approx equity - amount for wallet)
        new_eq = max(0.0, equity - amount)
        db.set_spot_sod_equity(new_eq, day=transfer_date)
        note_equity_peak(new_eq)
        logging.info(
            "  Spot transfer success %s: %.2f %s futures→spot (tranId=%s) — %s",
            transfer_date,
            amount,
            MARGIN_COIN,
            result.get("tranId"),
            detail,
        )
        _notify_decision(transfer_date, "success", decision, detail)
    except ExchangeClientError as exc:
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=amount,
            status="failed",
            available_before=available,
            sod_equity=sod,
            eod_equity=equity,
            day_pnl=decision.day_pnl,
            dd_pct=decision.dd_pct * 100,
            peak_equity=peak,
            reason="transfer",
            error=str(exc),
        )
        logging.error("  Spot transfer failed %s: %s", transfer_date, exc)
        _notify_decision(transfer_date, "failed", decision, str(exc))


def _execute_pct(ref_symbol: str, transfer_date: str) -> None:
    """Legacy fixed-% equity withdraw (kept for env SPOT_TRANSFER_MODE=pct)."""
    balance = fetch_futures_balance(ref_symbol)
    equity = float(balance.account_equity or 0)
    available = float(balance.available or 0)
    peak = note_equity_peak(equity)
    amount = math.floor(equity * (SPOT_TRANSFER_PCT / 100.0) * 100) / 100.0
    amount = min(amount, max(available, 0.0))
    decision = SkimDecision(
        amount=amount,
        reason="pct",
        day_pnl=0.0,
        dd_pct=0.0,
        sod=equity,
        equity=equity,
        peak=peak,
        available=available,
    )
    if amount < _MIN_TRANSFER_USDT:
        detail = _reason_label("below_min", decision)
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=amount,
            status="skipped",
            available_before=available,
            eod_equity=equity,
            peak_equity=peak,
            reason="below_min",
            error=detail,
        )
        return
    try:
        result = transfer_futures_to_spot(MARGIN_COIN, amount)
        spot_after = None
        try:
            spot_after = fetch_spot_balance(MARGIN_COIN)
        except ExchangeClientError:
            pass
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=amount,
            status="success",
            tran_id=str(result.get("tranId") or "") or None,
            available_before=available,
            spot_after=spot_after,
            eod_equity=equity,
            peak_equity=peak,
            reason="pct",
            error=_reason_label("pct", decision),
        )
        logging.info("  Spot transfer (pct) success: %.2f", amount)
    except ExchangeClientError as exc:
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=amount,
            status="failed",
            available_before=available,
            eod_equity=equity,
            peak_equity=peak,
            reason="pct",
            error=str(exc),
        )


def process_daily_spot_transfer(ref_symbol: str = "BTCUSDT") -> None:
    """Run once per VN day at/after EXECUTE_HHMM (default 07:00)."""
    if not is_enabled() or not has_credentials():
        return

    now = _vn_now()
    transfer_date = _vn_date_str(now)
    if db.has_transfer_decision_on_date(transfer_date):
        return

    execute_mins = _hhmm_to_minutes(SPOT_TRANSFER_EXECUTE_HHMM, (7, 0))
    if _minutes_of_day(now) < execute_mins:
        return

    # Never skim with unsynced SOD/peak after manual deposit/withdraw.
    if not sync_manual_transfers():
        if not db.has_transfer_decision_on_date(transfer_date):
            db.insert_spot_transfer(
                transfer_date=transfer_date,
                amount=0.0,
                status="skipped",
                reason="sync_failed",
                error="manual TRANSFER sync failed — deferred to next day",
            )
            logging.warning(
                "  Spot transfer deferred for %s — manual sync failed", transfer_date
            )
        return

    mode = SPOT_TRANSFER_MODE
    try:
        if mode == "skim":
            _execute_skim(ref_symbol, transfer_date)
        elif mode == "pct":
            _execute_pct(ref_symbol, transfer_date)
        else:
            logging.warning("SPOT_TRANSFER_MODE=%s unsupported — using skim", mode)
            _execute_skim(ref_symbol, transfer_date)
    except ExchangeClientError as exc:
        logging.warning("  Spot transfer skipped — balance/API error: %s", exc)


def check_risk_warnings(
    ref_symbol: str = "BTCUSDT",
    *,
    equity: float | None = None,
    maint_pct: float | None = None,
    initial_pct: float | None = None,
) -> list[dict]:
    """Update peak; Discord/dashboard warn chỉ khi DD hoặc maint > ngưỡng (mặc định 50%)."""
    del initial_pct  # kept for call-site compat; initial margin không warn
    warns: list[dict] = []
    if equity is None or maint_pct is None:
        if not has_credentials():
            return warns
        try:
            bal = fetch_futures_balance(ref_symbol)
            equity = float(bal.account_equity or 0) if equity is None else equity
            maint_pct = float(bal.maint_margin_pct or 0) if maint_pct is None else maint_pct
        except ExchangeClientError:
            return warns

    equity = float(equity or 0)
    maint = float(maint_pct or 0)
    if equity <= 0:
        return warns

    # Keep SOD/peak aligned with manual deposits/withdrawals before DD calc.
    sync_manual_transfers()

    peak = note_equity_peak(equity)
    dd = (peak - equity) / peak if peak > 0 else 0.0

    # ">" 50%: equality at exactly 50% does not warn
    if dd > SPOT_WARN_DD_PCT:
        suggest = max(0.0, peak - equity)
        warns.append(
            {
                "kind": "dd",
                "level": dd * 100,
                "message": (
                    f"DD từ peak {dd*100:.1f}% > {SPOT_WARN_DD_PCT*100:.0f}% "
                    f"(peak={peak:.0f}, equity={equity:.0f}). "
                    f"Khuyến nghị nạp thêm ~{suggest:.0f} USDT về peak (optional)."
                ),
                "suggest_usdt": suggest,
            }
        )
    if maint > SPOT_WARN_MAINT_PCT:
        warns.append(
            {
                "kind": "maint",
                "level": maint,
                "message": f"Maint margin {maint:.1f}% > {SPOT_WARN_MAINT_PCT:.0f}%",
                "suggest_usdt": 0.0,
            }
        )

    now = time.time()
    for w in warns:
        key = w["kind"]
        last = _last_warn_at.get(key, 0.0)
        if now - last < SPOT_WARN_COOLDOWN_SEC:
            continue
        _last_warn_at[key] = now
        try:
            from src.notify import notify_risk_warning

            notify_risk_warning(w["kind"], w["message"])
        except Exception as exc:  # noqa: BLE001
            logging.warning("Discord risk warn failed: %s", exc)
    return warns


def today_transfer_status() -> dict:
    transfer_date = _vn_date_str()
    rows = [
        row
        for row in db.get_spot_transfers(limit=30)
        if str(row["transfer_date"]) == transfer_date
    ]
    latest = rows[0] if rows else None
    peak = db.get_equity_peak()
    sod = db.get_spot_sod_equity()
    return {
        "date": transfer_date,
        "enabled": is_enabled(),
        "mode": SPOT_TRANSFER_MODE,
        "skim": SPOT_TRANSFER_SKIM,
        "day_cap_pct": SPOT_TRANSFER_DAY_CAP_PCT * 100,
        "dd_pause_pct": SPOT_TRANSFER_DD_PAUSE_PCT * 100,
        "execute_hhmm": SPOT_TRANSFER_EXECUTE_HHMM,
        "sod_equity": sod,
        "peak_equity": peak,
        "latest_status": str(latest["status"]) if latest else None,
        "latest_reason": str(latest["reason"] or latest["error"] or "") if latest else None,
        "latest_amount": float(latest["amount"]) if latest else None,
    }


def dashboard_payload(
    *,
    page: int = 1,
    page_size: int = 20,
    since_today: bool = True,
) -> dict:
    status = today_transfer_status()
    since = _vn_date_str() if since_today else None
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    raw_rows, total = db.get_spot_transfers_paged(
        since_date=since, page=page, page_size=page_size
    )
    rows = []
    for row in raw_rows:
        rows.append(
            {
                "id": int(row["id"]),
                "date": str(row["transfer_date"]),
                "amount": float(row["amount"] or 0),
                "status": str(row["status"]),
                "reason": str(row["reason"] or ""),
                "detail": str(row["error"] or ""),
                "day_pnl": float(row["day_pnl"]) if row["day_pnl"] is not None else None,
                "dd_pct": float(row["dd_pct"]) if row["dd_pct"] is not None else None,
                "sod": float(row["sod_equity"]) if row["sod_equity"] is not None else None,
                "eod": float(row["eod_equity"]) if row["eod_equity"] is not None else None,
                "peak": float(row["peak_equity"]) if row["peak_equity"] is not None else None,
                "spot_after": float(row["spot_after"]) if row["spot_after"] is not None else None,
                "tran_id": str(row["tran_id"] or ""),
                "created_at": str(row["created_at"] or ""),
            }
        )
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    # Live risk snapshot from bot_state (no forced REST)
    from src.bot_state import get_account_balance

    account = get_account_balance()
    equity = float(account.equity or 0)
    peak = db.get_equity_peak() or equity
    dd = ((peak - equity) / peak * 100) if peak > 0 and equity > 0 else 0.0
    risk = {
        "dd_pct": dd,
        "peak": peak,
        "maint_pct": account.maint_margin_pct,
        "initial_pct": account.initial_margin_pct,
        "warn_dd": dd > SPOT_WARN_DD_PCT * 100,
        "warn_maint": (account.maint_margin_pct or 0) > SPOT_WARN_MAINT_PCT,
        "suggest_topup": max(0.0, peak - equity) if dd > SPOT_WARN_DD_PCT * 100 else 0.0,
    }
    return {
        "status": status,
        "rows": rows,
        "risk": risk,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "total": total,
        "since": since,
    }

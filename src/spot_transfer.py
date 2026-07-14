"""Daily USDT transfer from futures wallet to spot."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from src import database as db
from src.config import (
    MARGIN_COIN,
    MARGIN_PREFLIGHT_MAX_CLOSES,
    SPOT_TRANSFER_AMOUNT,
    SPOT_TRANSFER_ENABLED,
    SPOT_TRANSFER_EXECUTE_HHMM,
    SPOT_TRANSFER_PREPARE_HHMM,
)
from src.exchange import (
    ExchangeClientError,
    fetch_futures_balance,
    fetch_side_mark_price,
    fetch_spot_balance,
    has_credentials,
    transfer_futures_to_spot,
)
from src.margin_preflight import (
    collect_leg_candidates,
    collect_pair_candidates,
    pick_best_leg,
    pick_best_pair,
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

_PREPARED_DATES: set[str] = set()


def _parse_hhmm(raw: str, fallback: tuple[int, int]) -> tuple[int, int]:
    text = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(text) == 3:
        text = f"0{text}"
    if len(text) != 4:
        return fallback
    hour = int(text[:2])
    minute = int(text[2:])
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


def get_transfer_amount() -> float:
    return db.get_spot_transfer_amount(SPOT_TRANSFER_AMOUNT)


def set_transfer_amount(amount: float) -> None:
    if amount <= 0:
        raise ValueError("amount must be positive")
    db.set_spot_transfer_amount(amount)


def is_enabled() -> bool:
    return db.is_spot_transfer_enabled(SPOT_TRANSFER_ENABLED)


def set_enabled(enabled: bool) -> None:
    db.set_spot_transfer_enabled(enabled)


def _lot_row_by_id(lot_id: int):
    for lot in db.get_all_open_pair_lots():
        if int(lot["id"]) == lot_id:
            return lot
    return None


def ensure_available_for_transfer(required: float, ref_symbol: str) -> tuple[bool, int]:
    """Close profitable / least-losing legs until futures available covers required."""
    if required <= 0:
        return True, 0
    if not has_credentials():
        return False, 0

    from src.rsi_trading import close_hedge_symbol, close_lot_leg

    closes = 0
    phase_b_only = False
    symbol = ref_symbol.upper()

    while closes < MARGIN_PREFLIGHT_MAX_CLOSES:
        balance = fetch_futures_balance(symbol)
        if balance.available >= required - 1e-9:
            if closes:
                logging.info(
                    "  Spot transfer preflight OK after %d close(s): available=%.2f >= %.2f",
                    closes,
                    balance.available,
                    required,
                )
            return True, closes

        logging.info(
            "  Spot transfer: available=%.2f < required=%.2f — freeing margin",
            balance.available,
            required,
        )

        if not phase_b_only:
            legs = collect_leg_candidates()
            best_leg = pick_best_leg(legs, symbol)
            if best_leg is not None:
                lot = _lot_row_by_id(best_leg.lot_id)
                if lot is None:
                    phase_b_only = True
                    continue
                mark = fetch_side_mark_price(best_leg.symbol)
                logging.info(
                    "  Spot transfer leg close %s %s lot #%d pnl≈%+.2f",
                    best_leg.symbol,
                    best_leg.side.upper(),
                    best_leg.lot_id,
                    best_leg.pnl_est,
                )
                close_lot_leg(
                    best_leg.symbol,
                    lot,
                    best_leg.side,
                    mark,
                    "spot_transfer_preflight_leg",
                )
                closes += 1
                continue
            phase_b_only = True

        pairs = collect_pair_candidates()
        best_pair = pick_best_pair(pairs, symbol)
        if best_pair is None:
            break
        mark = fetch_side_mark_price(best_pair.symbol)
        logging.info(
            "  Spot transfer pair close %s net_pnl≈%+.2f",
            best_pair.symbol,
            best_pair.net_pnl,
        )
        close_hedge_symbol(best_pair.symbol, mark)
        closes += 1

    balance = fetch_futures_balance(symbol)
    ok = balance.available >= required - 1e-9
    if not ok:
        logging.warning(
            "  Spot transfer preflight failed: available=%.2f < required=%.2f after %d close(s)",
            balance.available,
            required,
            closes,
        )
    return ok, closes


def _prepare_for_transfer(ref_symbol: str, amount: float, transfer_date: str) -> None:
    if transfer_date in _PREPARED_DATES:
        return
    ok, closes = ensure_available_for_transfer(amount, ref_symbol)
    _PREPARED_DATES.add(transfer_date)
    if ok:
        logging.info(
            "  Spot transfer prepare done for %s (amount=%.2f, closes=%d)",
            transfer_date,
            amount,
            closes,
        )
    else:
        logging.warning(
            "  Spot transfer prepare incomplete for %s (amount=%.2f, closes=%d)",
            transfer_date,
            amount,
            closes,
        )


def _execute_transfer(ref_symbol: str, amount: float, transfer_date: str) -> None:
    if db.has_successful_transfer_on_date(transfer_date):
        return

    ok, closes = ensure_available_for_transfer(amount, ref_symbol)
    balance = fetch_futures_balance(ref_symbol)
    available_before = balance.available

    if not ok:
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=amount,
            status="failed",
            available_before=available_before,
            legs_closed=closes,
            error=f"insufficient available ({available_before:.4f} < {amount:.4f})",
        )
        return

    try:
        result = transfer_futures_to_spot(MARGIN_COIN, amount)
        spot_after: float | None
        try:
            spot_after = fetch_spot_balance(MARGIN_COIN)
        except ExchangeClientError as exc:
            logging.warning("  Spot balance fetch after transfer failed: %s", exc)
            spot_after = None
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=amount,
            status="success",
            tran_id=str(result.get("tranId") or "") or None,
            available_before=available_before,
            spot_after=spot_after,
            legs_closed=closes,
        )
        logging.info(
            "  Spot transfer success: %.2f %s futures→spot (date=%s, tranId=%s, closes=%d)",
            amount,
            MARGIN_COIN,
            transfer_date,
            result.get("tranId"),
            closes,
        )
        if spot_after is not None:
            try:
                db.insert_spot_snapshot(spot_after)
            except Exception as exc:  # noqa: BLE001
                logging.warning("  Spot snapshot after transfer failed: %s", exc)
    except ExchangeClientError as exc:
        db.insert_spot_transfer(
            transfer_date=transfer_date,
            amount=amount,
            status="failed",
            available_before=available_before,
            legs_closed=closes,
            error=str(exc),
        )
        logging.error("  Spot transfer failed: %s", exc)


def process_daily_spot_transfer(ref_symbol: str) -> None:
    """Run once per cycle: prepare at 06:55 VN, transfer from 07:00 VN (1 success/day)."""
    if not is_enabled():
        return
    if not has_credentials():
        return

    now = _vn_now()
    transfer_date = _vn_date_str(now)
    if db.has_successful_transfer_on_date(transfer_date):
        return

    amount = get_transfer_amount()
    if amount <= 0:
        return

    now_mins = _minutes_of_day(now)
    prepare_mins = _hhmm_to_minutes(SPOT_TRANSFER_PREPARE_HHMM, (6, 55))
    execute_mins = _hhmm_to_minutes(SPOT_TRANSFER_EXECUTE_HHMM, (7, 0))

    if now_mins >= prepare_mins and now_mins < execute_mins:
        _prepare_for_transfer(ref_symbol, amount, transfer_date)
        return

    if now_mins >= execute_mins:
        _execute_transfer(ref_symbol, amount, transfer_date)


def today_transfer_status() -> dict:
    transfer_date = _vn_date_str()
    rows = [
        row
        for row in db.get_spot_transfers(limit=20)
        if str(row["transfer_date"]) == transfer_date
    ]
    success = next((row for row in rows if row["status"] == "success"), None)
    latest = rows[0] if rows else None
    return {
        "date": transfer_date,
        "enabled": is_enabled(),
        "amount": get_transfer_amount(),
        "success": success is not None,
        "latest_status": str(latest["status"]) if latest else None,
        "prepare_hhmm": SPOT_TRANSFER_PREPARE_HHMM,
        "execute_hhmm": SPOT_TRANSFER_EXECUTE_HHMM,
    }

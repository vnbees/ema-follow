"""Free available balance before opening a new hedge pair."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src import database as db
from src.config import (
    MARGIN_PREFLIGHT_BUFFER_PCT,
    MARGIN_PREFLIGHT_ENABLED,
    MARGIN_PREFLIGHT_MAX_CLOSES,
    TRADING_ENABLED,
)
from src.exchange import ExchangeClientError, fetch_futures_balance, fetch_side_mark_price, has_credentials
from src.order_sizing import compute_entry_margin_usdt
from src.rsi import RsiSnapshot


@dataclass
class LegCandidate:
    symbol: str
    side: str
    lot_id: int
    size: float
    entry: float
    pnl_est: float


@dataclass
class PairCandidate:
    symbol: str
    net_pnl: float


def required_available_for_pair(equity: float) -> float:
    margin_per_leg = compute_entry_margin_usdt(equity)
    return 2 * margin_per_leg * (1 + MARGIN_PREFLIGHT_BUFFER_PCT / 100.0)


def _estimate_leg_pnl(side: str, entry: float, mark: float, size: float) -> float:
    if side == "long":
        return (mark - entry) * size
    return (entry - mark) * size


def collect_leg_candidates() -> list[LegCandidate]:
    marks: dict[str, float] = {}
    candidates: list[LegCandidate] = []
    for lot in db.get_all_open_pair_lots():
        symbol = str(lot["symbol"]).upper()
        if symbol not in marks:
            try:
                marks[symbol] = fetch_side_mark_price(symbol)
            except ExchangeClientError:
                marks[symbol] = 0.0
        mark = marks[symbol]
        lot_id = int(lot["id"])
        if lot["long_status"] == "open":
            entry = float(lot["long_entry"])
            size = float(lot["long_size"])
            pnl = _estimate_leg_pnl("long", entry, mark, size)
            candidates.append(
                LegCandidate(symbol, "long", lot_id, size, entry, pnl),
            )
        if lot["short_status"] == "open":
            entry = float(lot["short_entry"])
            size = float(lot["short_size"])
            pnl = _estimate_leg_pnl("short", entry, mark, size)
            candidates.append(
                LegCandidate(symbol, "short", lot_id, size, entry, pnl),
            )
    candidates.sort(key=lambda row: row.pnl_est, reverse=True)
    return candidates


def pick_best_leg(candidates: list[LegCandidate], target_symbol: str) -> LegCandidate | None:
    if not candidates:
        return None
    target_symbol = target_symbol.upper()
    other = [row for row in candidates if row.symbol != target_symbol]
    pool = other if other else candidates
    return max(pool, key=lambda row: row.pnl_est)


def collect_pair_candidates() -> list[PairCandidate]:
    from src.exchange import fetch_side_unrealized_pnl, fetch_symbol_positions

    symbols: set[str] = set()
    for lot in db.get_all_open_pair_lots():
        symbols.add(str(lot["symbol"]).upper())
    candidates: list[PairCandidate] = []
    for symbol in symbols:
        try:
            positions = fetch_symbol_positions(symbol)
            if positions["long"].size <= 0 and positions["short"].size <= 0:
                continue
            net = fetch_side_unrealized_pnl(symbol, "long") + fetch_side_unrealized_pnl(symbol, "short")
        except ExchangeClientError:
            continue
        candidates.append(PairCandidate(symbol, net))
    candidates.sort(key=lambda row: row.net_pnl, reverse=True)
    return candidates


def pick_best_pair(candidates: list[PairCandidate], target_symbol: str) -> PairCandidate | None:
    if not candidates:
        return None
    target_symbol = target_symbol.upper()
    other = [row for row in candidates if row.symbol != target_symbol]
    pool = other if other else candidates
    return max(pool, key=lambda row: row.net_pnl)


def ensure_available_for_pair(symbol: str, snap: RsiSnapshot, trigger: str) -> bool:
    """Close legs/pairs until available covers a new L+S pair, or give up."""
    if not MARGIN_PREFLIGHT_ENABLED or not TRADING_ENABLED or not has_credentials():
        return True

    from src.rsi_trading import close_hedge_symbol, close_lot_leg

    symbol = symbol.upper()
    closes = 0
    phase_b_only = False

    while closes < MARGIN_PREFLIGHT_MAX_CLOSES:
        balance = fetch_futures_balance(symbol)
        required = required_available_for_pair(balance.account_equity)
        if balance.available >= required - 1e-9:
            if closes:
                logging.info(
                    "  [%s] Preflight OK after %d close(s): available=%.2f >= required=%.2f",
                    symbol,
                    closes,
                    balance.available,
                    required,
                )
            return True

        logging.info(
            "  [%s] Preflight: available=%.2f < required=%.2f — freeing margin (%s)",
            symbol,
            balance.available,
            required,
            trigger,
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
                    "  Preflight leg close %s %s lot #%d pnl≈%+.2f",
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
                    f"{trigger}_margin_preflight_leg",
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
            "  Preflight pair close %s net_pnl≈%+.2f",
            best_pair.symbol,
            best_pair.net_pnl,
        )
        close_hedge_symbol(best_pair.symbol, mark)
        closes += 1

    balance = fetch_futures_balance(symbol)
    required = required_available_for_pair(balance.account_equity)
    if balance.available >= required - 1e-9:
        logging.info(
            "  [%s] Preflight OK after %d close(s): available=%.2f >= required=%.2f",
            symbol,
            closes,
            balance.available,
            required,
        )
        return True

    logging.warning(
        "  [%s] Preflight failed: available=%.2f < required=%.2f after %d close(s)",
        symbol,
        balance.available,
        required,
        closes,
    )
    return False


def _lot_row_by_id(lot_id: int):
    for lot in db.get_all_open_pair_lots():
        if int(lot["id"]) == lot_id:
            return lot
    return None

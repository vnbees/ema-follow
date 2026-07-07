"""Maintenance margin guard — tiered risk response per margin_guard plan."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src import database as db
from src.bot_state import is_trading_enabled, update_account_balance
from src.config import (
    MARGIN_DEPOSIT_TARGET_PCT,
    MARGIN_ELEVATED_CYCLE_LIMIT,
    MARGIN_GUARD_ENABLED,
    MARGIN_HIGH_CYCLE_LIMIT,
    MARGIN_HIGH_TP_PCT,
    MARGIN_IMPROVEMENT_PCT,
    MARGIN_MAINT_CRITICAL_PCT,
    MARGIN_MAINT_DELEVERAGE_PCT,
    MARGIN_MAINT_HIGH_PCT,
    MARGIN_MAINT_OK_PCT,
    MARGIN_MAINT_WARN_PCT,
    PAIR_PROFIT_TARGET_PCT,
    TRADING_ENABLED,
)
from src.exchange import ExchangeClientError, fetch_futures_balance, has_credentials

_CONFIG_TRADING_ENABLED = TRADING_ENABLED


def _trading_enabled() -> bool:
    if TRADING_ENABLED != _CONFIG_TRADING_ENABLED:
        return bool(TRADING_ENABLED)
    return is_trading_enabled()


@dataclass
class MarginStats:
    equity: float = 0.0
    available: float = 0.0
    maint_margin: float = 0.0
    initial_margin: float = 0.0
    maint_margin_pct: float = 0.0
    initial_margin_pct: float = 0.0


@dataclass
class MarginGuardState:
    tier: str = "ok"
    stats: MarginStats = field(default_factory=MarginStats)
    block_new_entries: bool = False
    effective_tp_pct: float = PAIR_PROFIT_TARGET_PCT
    elevated_cycles: int = 0
    high_cycles: int = 0
    scenario: str = ""
    suggest_deposit_usdt: float | None = None
    skip_cycle: bool = False


_guard_state = MarginGuardState()
_elevated_since_pct: float | None = None
_high_since_pct: float | None = None


def reset_margin_guard_state() -> None:
    """Reset in-memory guard counters (tests / manual)."""
    global _guard_state, _elevated_since_pct, _high_since_pct
    _guard_state = MarginGuardState()
    _elevated_since_pct = None
    _high_since_pct = None


def get_margin_guard_state() -> MarginGuardState:
    return _guard_state


def fetch_account_margin_stats(symbol: str) -> MarginStats | None:
    if not has_credentials():
        return None
    try:
        balance = fetch_futures_balance(symbol)
    except ExchangeClientError as exc:
        logging.warning("  Margin stats fetch failed: %s", exc)
        return None
    return MarginStats(
        equity=balance.account_equity,
        available=balance.available,
        maint_margin=balance.total_maint_margin,
        initial_margin=balance.total_initial_margin,
        maint_margin_pct=balance.maint_margin_pct,
        initial_margin_pct=balance.initial_margin_pct,
    )


def suggest_deposit_usdt(maint: float, equity: float, target_pct: float = MARGIN_DEPOSIT_TARGET_PCT) -> float | None:
    if maint <= 0 or equity <= 0 or target_pct <= 0:
        return None
    needed_equity = maint / (target_pct / 100)
    deposit = needed_equity - equity
    return deposit if deposit > 0.01 else None


def _tier_for_pct(maint_pct: float) -> str:
    if maint_pct > MARGIN_MAINT_CRITICAL_PCT:
        return "critical"
    if maint_pct > MARGIN_MAINT_HIGH_PCT:
        return "high"
    if maint_pct > MARGIN_MAINT_WARN_PCT:
        return "elevated"
    if maint_pct > MARGIN_MAINT_OK_PCT:
        return "watch"
    return "ok"


def should_block_new_entries() -> bool:
    return _guard_state.block_new_entries


def effective_tp_pct() -> float:
    return _guard_state.effective_tp_pct


def _resolve_tier(stats: MarginStats) -> str:
    base = _tier_for_pct(stats.maint_margin_pct)
    global _elevated_since_pct, _high_since_pct

    if base == "critical":
        return "critical"

    if base == "high":
        _high_since_pct = _high_since_pct or stats.maint_margin_pct
        return "high"

    if base == "elevated":
        if _elevated_since_pct is None:
            _elevated_since_pct = stats.maint_margin_pct
        if _guard_state.elevated_cycles >= MARGIN_ELEVATED_CYCLE_LIMIT:
            return "high"
        return "elevated"

    if _guard_state.tier in ("elevated", "high") and stats.maint_margin_pct <= MARGIN_MAINT_WARN_PCT:
        _elevated_since_pct = None
        _high_since_pct = None
        _guard_state.elevated_cycles = 0
        _guard_state.high_cycles = 0

    return base


def _update_counters(stats: MarginStats, tier: str) -> None:
    global _elevated_since_pct, _high_since_pct

    if tier == "elevated":
        _guard_state.elevated_cycles += 1
        if _elevated_since_pct is not None:
            improved = _elevated_since_pct - stats.maint_margin_pct
            if improved >= MARGIN_IMPROVEMENT_PCT:
                _elevated_since_pct = stats.maint_margin_pct
                _guard_state.elevated_cycles = 0
        return

    if tier == "high":
        _guard_state.high_cycles += 1
        if _high_since_pct is not None:
            improved = _high_since_pct - stats.maint_margin_pct
            if improved >= MARGIN_IMPROVEMENT_PCT:
                _high_since_pct = stats.maint_margin_pct
                _guard_state.high_cycles = 0
        return

    _guard_state.elevated_cycles = 0
    _guard_state.high_cycles = 0
    _elevated_since_pct = None
    _high_since_pct = None


def _scenario_label(tier: str) -> str:
    labels = {
        "ok": "Bình thường",
        "watch": "Theo dõi margin",
        "elevated": "Kịch bản 1: chặn mở mới, TP 2%",
        "high": "Kịch bản 2–3: TP 1%, có thể đóng cặp L+S",
        "critical": "Kịch bản 5: đóng hết khẩn cấp",
    }
    return labels.get(tier, tier)


def process_margin_guard_cycle(symbol: str) -> MarginGuardState:
    """Run at start of each 5m cycle. May deleverage or liquidate."""
    global _guard_state

    _guard_state.effective_tp_pct = PAIR_PROFIT_TARGET_PCT
    _guard_state.block_new_entries = False
    _guard_state.skip_cycle = False
    _guard_state.suggest_deposit_usdt = None

    if not MARGIN_GUARD_ENABLED or not _trading_enabled() or not has_credentials():
        _guard_state.tier = "ok"
        _guard_state.scenario = "Margin guard tắt"
        return _guard_state

    stats = fetch_account_margin_stats(symbol)
    if stats is None or stats.equity <= 0:
        return _guard_state

    tier = _resolve_tier(stats)
    _update_counters(stats, tier)

    if tier == "critical":
        _guard_state.tier = tier
        _guard_state.stats = stats
        _guard_state.block_new_entries = True
        _guard_state.scenario = _scenario_label(tier)
        _guard_state.suggest_deposit_usdt = suggest_deposit_usdt(
            stats.maint_margin, stats.equity,
        )
        logging.warning(
            "  Margin CRITICAL: maint=%.2f%% equity=%.2f — liquidating all hedge pairs",
            stats.maint_margin_pct,
            stats.equity,
        )
        from src.rsi_trading import liquidate_all_hedge_pairs
        from src.rsi_positions import get_managed_symbols

        liquidate_all_hedge_pairs(get_managed_symbols() or [symbol])
        _guard_state.skip_cycle = True
        return _guard_state

    if tier == "high":
        _guard_state.effective_tp_pct = MARGIN_HIGH_TP_PCT
        _guard_state.block_new_entries = True
        should_deleverage = (
            stats.maint_margin_pct > MARGIN_MAINT_DELEVERAGE_PCT
            or _guard_state.high_cycles >= MARGIN_HIGH_CYCLE_LIMIT
        )
        if should_deleverage:
            from src.rsi_trading import deleverage_one_symbol

            closed = deleverage_one_symbol()
            if closed:
                logging.info(
                    "  Margin HIGH: deleveraged %s (maint=%.2f%%)",
                    closed,
                    stats.maint_margin_pct,
                )
        logging.warning(
            "  Margin HIGH: maint=%.2f%% — block entries, TP=%.1f%%",
            stats.maint_margin_pct,
            _guard_state.effective_tp_pct,
        )
    elif tier == "elevated":
        _guard_state.block_new_entries = True
        logging.warning(
            "  Margin ELEVATED: maint=%.2f%% — block scan/stack/reopen, TP=%.1f%%",
            stats.maint_margin_pct,
            _guard_state.effective_tp_pct,
        )
    elif tier == "watch":
        logging.info(
            "  Margin WATCH: maint=%.2f%% (ngưỡng cảnh báo %.1f%%)",
            stats.maint_margin_pct,
            MARGIN_MAINT_WARN_PCT,
        )

    if stats.maint_margin_pct > MARGIN_MAINT_WARN_PCT:
        _guard_state.suggest_deposit_usdt = suggest_deposit_usdt(
            stats.maint_margin, stats.equity,
        )

    _guard_state.tier = tier
    _guard_state.stats = stats
    _guard_state.scenario = _scenario_label(tier)
    return _guard_state


def refresh_margin_dashboard_fields(
    available: float,
    equity: float,
    margin_coin: str,
    last_updated: str,
) -> None:
    """Push margin guard fields into account balance for dashboard."""
    state = _guard_state
    stats = state.stats
    update_account_balance(
        available,
        equity,
        margin_coin,
        last_updated,
        maint_margin_pct=stats.maint_margin_pct if stats.equity > 0 else None,
        initial_margin_pct=stats.initial_margin_pct if stats.equity > 0 else None,
        margin_guard_tier=state.tier,
        margin_guard_scenario=state.scenario,
        suggest_deposit_usdt=state.suggest_deposit_usdt,
        effective_tp_pct=state.effective_tp_pct,
    )

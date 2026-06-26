from src.config import (
    LEVERAGE,
    LEGACY_MARGIN_USDT,
    ORDER_MARGIN_MIN_USDT,
    ORDER_MARGIN_PCT,
)


def compute_entry_margin_usdt(equity: float) -> float:
    if equity <= 0:
        return ORDER_MARGIN_MIN_USDT
    return max(ORDER_MARGIN_MIN_USDT, equity * ORDER_MARGIN_PCT / 100)


def margin_to_notional(margin_usdt: float) -> float:
    return margin_usdt * LEVERAGE


def trade_margin_usdt(row) -> float:
    if row is not None and row["margin_usdt"] is not None:
        return float(row["margin_usdt"])
    return LEGACY_MARGIN_USDT

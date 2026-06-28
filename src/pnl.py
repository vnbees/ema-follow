from src.config import LEGACY_MARGIN_USDT
from src.order_sizing import trade_margin_usdt


def total_margin_deployed(margin_usdt: float, dca_count: int) -> float:
    return margin_usdt * (1 + dca_count)


def roi_pct(pnl_usdt: float | None, margin_deployed: float) -> float | None:
    if pnl_usdt is None or margin_deployed <= 0:
        return None
    return pnl_usdt / margin_deployed * 100


def leg_unrealized_pnl(side: str, entry: float, size: float, mark: float) -> float | None:
    if entry <= 0 or size <= 0 or mark <= 0:
        return None
    if side == "long":
        return (mark - entry) * size
    return (entry - mark) * size


def leg_realized_pnl(
    side: str,
    entry: float,
    size: float,
    *,
    realized_pnl_usdt: float | None,
    close_price: float | None,
) -> float | None:
    if realized_pnl_usdt is not None:
        return float(realized_pnl_usdt)
    if close_price and close_price > 0 and entry > 0 and size > 0:
        return leg_unrealized_pnl(side, entry, size, close_price)
    return None


def estimate_tp_pnl_usdt(entry: float, size: float, target_pct: float) -> float | None:
    """Lower-bound PnL when leg closed at profit target but DB has no fill."""
    if entry <= 0 or size <= 0 or target_pct <= 0:
        return None
    return entry * target_pct / 100 * size


def margin_from_trade_row(row) -> float:
    if row is None:
        return LEGACY_MARGIN_USDT
    return trade_margin_usdt(row)


def dca_count_from_row(row) -> int:
    if row is None:
        return 0
    if "dca_count" not in row.keys() or row["dca_count"] is None:
        return 0
    return int(row["dca_count"])

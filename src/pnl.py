from src.config import LEGACY_MARGIN_USDT
from src.order_sizing import trade_margin_usdt


def total_margin_deployed(margin_usdt: float, dca_count: int) -> float:
    return margin_usdt * (1 + dca_count)


def roi_pct(pnl_usdt: float | None, margin_deployed: float) -> float | None:
    if pnl_usdt is None or margin_deployed <= 0:
        return None
    return pnl_usdt / margin_deployed * 100


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

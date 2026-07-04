import math

from src.exchange.types import ContractSpec


def _round_down(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.floor(value * factor) / factor


def _round_up(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.ceil(value * factor - 1e-12) / factor


def notional_to_size(notional_usdt: float, price: float, spec: ContractSpec) -> str:
    target_usdt = max(notional_usdt, spec.min_trade_usdt)
    raw_size = target_usdt / price
    size = _round_up(raw_size, spec.volume_place)
    if size < spec.min_trade_num:
        size = spec.min_trade_num
    step = 10 ** (-spec.volume_place)
    while size * price < spec.min_trade_usdt - 1e-9:
        size = _round_up(size + step, spec.volume_place)
    if spec.volume_place == 0:
        return str(int(size))
    formatted = f"{size:.{spec.volume_place}f}".rstrip("0").rstrip(".")
    return formatted or str(spec.min_trade_num)


def format_price(price: float, spec: ContractSpec) -> str:
    rounded = round(price, spec.price_place)
    return f"{rounded:.{spec.price_place}f}"


def format_size(size: float, spec: ContractSpec) -> str:
    rounded = round(size, spec.volume_place)
    if rounded < spec.min_trade_num:
        rounded = spec.min_trade_num
    if spec.volume_place == 0:
        return str(int(rounded))
    formatted = f"{rounded:.{spec.volume_place}f}".rstrip("0").rstrip(".")
    return formatted or str(spec.min_trade_num)

"""Symbol filters for volume scan / watch list."""


def is_scan_symbol(symbol: str) -> bool:
    """USDT-M perpetuals only; exclude USDC-quoted and USDC-related pairs."""
    s = symbol.upper().strip()
    if not s.endswith("USDT") or "_" in s:
        return False
    if s.endswith("USDC"):
        return False
    if "USDC" in s:
        return False
    return True


def is_tradeable_symbol(symbol: str) -> bool:
    """Alias: symbols the bot may open or stack."""
    return is_scan_symbol(symbol)

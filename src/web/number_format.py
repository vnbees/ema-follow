"""Dashboard number formatting — enough precision for low-priced symbols."""


def format_dashboard_price(value: float | None) -> str:
    if value is None:
        return "—"
    v = float(value)
    av = abs(v)
    if av >= 1000:
        return f"{v:.2f}"
    if av >= 1:
        return f"{v:.4f}"
    return f"{v:.6f}"


def format_dashboard_size(value: float | None) -> str:
    if value is None:
        return "—"
    v = float(value)
    av = abs(v)
    if av >= 100:
        return f"{v:.2f}"
    if av >= 1:
        return f"{v:.4f}"
    return f"{v:.6f}"


def format_dashboard_pnl(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.4f}"

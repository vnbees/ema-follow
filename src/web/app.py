from pathlib import Path
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src import database as db
from src.config import (
    DASHBOARD_COOKIE_SECURE,
    DASHBOARD_PASSWORD,
    DASHBOARD_SESSION_SECRET,
    DASHBOARD_USERNAME,
    EXCHANGE_DISPLAY_NAME,
    LEVERAGE,
    MARGIN_MODE,
)
from src.bot_state import (
    get_account_balance,
    get_last_cycle_at,
    is_trading_enabled,
    set_trading_enabled,
)
from src.web.number_format import format_dashboard_pnl, format_dashboard_price
from src.web.time_format import format_vn_time

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["vn_time"] = format_vn_time
templates.env.filters["dash_price"] = format_dashboard_price
templates.env.filters["dash_pnl"] = format_dashboard_pnl

app = FastAPI(title=f"{EXCHANGE_DISPLAY_NAME} Donchian Trend Bot Dashboard")

_PUBLIC_PATHS = frozenset({"/login"})
_SESSION_SECRET = DASHBOARD_SESSION_SECRET or secrets.token_urlsafe(48)

_EQUITY_RANGES = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _credentials_configured() -> bool:
    return bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD)


def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("user"))


def _verify_credentials(username: str, password: str) -> bool:
    if not _credentials_configured():
        return False
    user_ok = secrets.compare_digest(username.strip(), DASHBOARD_USERNAME)
    pass_ok = secrets.compare_digest(password, DASHBOARD_PASSWORD)
    return user_ok and pass_ok


@app.middleware("http")
async def require_dashboard_login(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC_PATHS:
        return await call_next(request)
    if _is_logged_in(request):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return RedirectResponse(url="/login", status_code=303)


app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    session_cookie="dashboard_session",
    max_age=7 * 24 * 60 * 60,
    same_site="lax",
    https_only=DASHBOARD_COOKIE_SECURE,
)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    if _is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "exchange_name": EXCHANGE_DISPLAY_NAME,
            "error": error,
            "auth_configured": _credentials_configured(),
        },
    )


@app.post("/login", response_model=None)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if _verify_credentials(username, password):
        request.session["user"] = DASHBOARD_USERNAME
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "exchange_name": EXCHANGE_DISPLAY_NAME,
            "error": "Email hoặc mật khẩu không đúng.",
            "auth_configured": _credentials_configured(),
        },
        status_code=401,
    )


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def _equity_since_for_range(range_key: str) -> datetime:
    delta = _EQUITY_RANGES.get(range_key, _EQUITY_RANGES["7d"])
    return datetime.now(timezone.utc) - delta


def build_equity_history_payload(range_key: str) -> dict:
    key = range_key if range_key in _EQUITY_RANGES else "7d"
    rows = db.get_equity_snapshots(_equity_since_for_range(key))
    account = get_account_balance()
    return {
        "range": key,
        "margin_coin": account.margin_coin or "USDT",
        "baseline_equity": db.get_baseline_equity(),
        "points": [
            {
                "time_vn": format_vn_time(str(row["recorded_at"])),
                "equity": float(row["equity"]),
                "available": float(row["available"]),
            }
            for row in rows
        ],
    }


def _fetch_symbol_marks(symbols: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    try:
        from src.exchange.binance_ws import get_mark_from_ws
    except Exception:  # noqa: BLE001
        return result
    for symbol in symbols:
        mark = 0.0
        try:
            cached = get_mark_from_ws(symbol)
            if cached is not None and cached > 0:
                mark = float(cached)
        except Exception:  # noqa: BLE001
            mark = 0.0
        result[symbol] = mark
    return result


def _lot_opened_epoch(opened_at: str) -> float:
    raw = (opened_at or "").strip()
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _lot_age_label(hours: float) -> str:
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _row_float(row, key: str, default: float = 0.0) -> float:
    try:
        raw = row[key]
    except (KeyError, IndexError):
        return default
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _duration_vi(seconds: float) -> str:
    total_m = max(0, int(seconds // 60))
    days, rem = divmod(total_m, 60 * 24)
    hours, minutes = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}n")
    if hours:
        parts.append(f"{hours}g")
    if minutes or not parts:
        parts.append(f"{minutes}ph")
    return " ".join(parts)


def _margin_mode_label() -> str:
    mode = (MARGIN_MODE or "crossed").lower()
    tag = "Cross" if mode in {"crossed", "cross"} else "Isolated"
    return f"{tag} {LEVERAGE}x"


def _serialize_lot(row, marks: dict[str, float], *, now_ts: float) -> dict:
    from src.donchian import store
    from src.donchian.trading import realized_pnl

    symbol = str(row["symbol"])
    side = str(row["side"])
    entry = float(row["entry_px"] or 0)
    size = float(row["size"] or 0)
    status = str(row["status"])
    margin = _row_float(row, "margin_usdt")
    fee_open = _row_float(row, "fee_open_usdt")
    fee_close = _row_float(row, "fee_close_usdt")
    fee_total = fee_open + fee_close
    mark = marks.get(symbol, 0.0)
    opened_raw = str(row["opened_at"] or "")
    closed_raw = str(row["closed_at"] or "") if status == "closed" else ""
    opened_epoch = _lot_opened_epoch(opened_raw) if opened_raw else 0.0
    closed_epoch = _lot_opened_epoch(closed_raw) if closed_raw else 0.0
    hold_end = closed_epoch if closed_epoch > 0 else now_ts
    duration_sec = max(0.0, hold_end - opened_epoch) if opened_epoch > 0 else 0.0
    age_h = max(0.0, (now_ts - opened_epoch) / 3600) if opened_epoch > 0 else 0.0
    unreal = None
    roi_pct = None
    if status == "open" and mark > 0 and size > 0 and entry > 0:
        unreal = realized_pnl(side, entry, mark, size)
        if margin > 0:
            roi_pct = unreal / margin * 100
        elif entry * size > 0:
            roi_pct = unreal / (entry * size) * 100
    closed_pnl = row["pnl_usdt"]
    if status == "closed" and closed_pnl is not None and margin > 0:
        roi_pct = float(closed_pnl) / margin * 100
    elif status == "closed" and closed_pnl is not None and entry > 0 and size > 0:
        roi_pct = float(closed_pnl) / (entry * size) * 100
    reason = str(row["close_reason"] or "")
    trend = str(row["trend"] or "")
    tp_band = float(row["tp_band"] or 0)
    return {
        "id": int(row["id"]),
        "symbol": symbol,
        "side": side,
        "side_label": "Mua" if side == "long" else "Bán",
        "trend": trend,
        "trend_label": f"Trend {trend.upper()}" if trend else "—",
        "zone": trend,
        "zone_label": f"Trend {trend.upper()}" if trend else "—",
        "status": status,
        "status_label": "Đã đóng" if status == "closed" else "Đang mở",
        "margin_mode": _margin_mode_label(),
        "entry": entry,
        "tp": tp_band,
        "tp_band": tp_band,
        "size": size,
        "margin_usdt": margin,
        "close_price": row["close_px"] if "close_px" in row.keys() else None,
        "close_reason": reason,
        "close_reason_label": store.REASON_LABELS.get(reason, reason),
        "pnl_usdt": closed_pnl,
        "pnl_pct": roi_pct,
        "fee_open_usdt": fee_open,
        "fee_close_usdt": fee_close,
        "fee_usdt": fee_total,
        "unrealized_pnl": unreal,
        "mark": mark,
        "age_hours": age_h,
        "age_label": _lot_age_label(age_h),
        "duration_label": _duration_vi(duration_sec),
        "exit_status": f"TP band: {tp_band:.4f}" if status == "open" and tp_band > 0 else "",
        "opened_at": format_vn_time(opened_raw) if opened_raw else "",
        "closed_at": format_vn_time(closed_raw) if closed_raw else "",
    }


def _donchian_summary() -> dict:
    from src.donchian import store
    from src.donchian.trading import realized_pnl

    open_rows = store.get_open_lots()
    symbols = sorted({str(row["symbol"]) for row in open_rows})
    marks = _fetch_symbol_marks(symbols)
    now_ts = datetime.now(timezone.utc).timestamp()
    open_unrealized = 0.0
    for row in open_rows:
        mark = marks.get(str(row["symbol"]), 0.0)
        if mark <= 0:
            continue
        open_unrealized += realized_pnl(
            str(row["side"]),
            float(row["entry_px"] or 0),
            mark,
            float(row["size"] or 0),
        )
    closed_realized = store.sum_closed_pnl()
    return {
        "open_count": store.count_open(),
        "closed_count": store.count_closed(),
        "pending_count": 0,
        "summary": {
            "open_unrealized": open_unrealized,
            "closed_realized": closed_realized,
            "total_pnl": open_unrealized + closed_realized,
        },
        "marks": marks,
        "now_ts": now_ts,
    }


def _donchian_trades_payload(*, status: str, page: int, page_size: int) -> dict:
    from src.donchian import store

    rows, total = store.list_lots_paged(status=status, page=page, page_size=page_size)
    summary = _donchian_summary()
    marks = summary["marks"]
    now_ts = summary["now_ts"]
    if status == "open":
        extra = {str(row["symbol"]) for row in rows}
        missing = [s for s in extra if s not in marks]
        if missing:
            marks.update(_fetch_symbol_marks(missing))
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return {
        "status": status,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "trades": [_serialize_lot(row, marks, now_ts=now_ts) for row in rows],
        "open_count": summary["open_count"],
        "closed_count": summary["closed_count"],
        "pending_count": 0,
        "summary": summary["summary"],
    }


def _dashboard_context() -> dict:
    account = get_account_balance()
    open_payload = _donchian_trades_payload(status="open", page=1, page_size=50)
    closed_payload = _donchian_trades_payload(status="closed", page=1, page_size=50)
    from src.donchian import store

    daily = store.daily_stats(30)
    return {
        "exchange_name": EXCHANGE_DISPLAY_NAME,
        "bot_title": "Donchian Trend",
        "account": account,
        "last_cycle_at": get_last_cycle_at(),
        "trading_enabled": is_trading_enabled(),
        "rsi_rev": {
            "open_count": open_payload["open_count"],
            "closed_count": open_payload["closed_count"],
            "pending_count": 0,
            "summary": open_payload["summary"],
            "open_trades": open_payload["trades"],
            "closed_trades": closed_payload["trades"],
            "open_page": open_payload,
            "closed_page": closed_payload,
            "daily": daily,
        },
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", _dashboard_context())


@app.get("/api/status")
def api_status() -> dict:
    account = get_account_balance()
    donchian = _donchian_summary()
    rate_limited = False
    rate_limit_remaining_sec = 0.0
    try:
        from src.exchange import binance as binance_mod

        rate_limited = binance_mod.is_rate_limited()
        rate_limit_remaining_sec = binance_mod.rate_limit_remaining_sec()
    except Exception:  # noqa: BLE001
        pass
    return {
        "account": {
            "available": account.available,
            "equity": account.equity,
            "margin_coin": account.margin_coin,
            "last_updated": format_vn_time(account.last_updated),
            "maint_margin_pct": account.maint_margin_pct,
            "initial_margin_pct": account.initial_margin_pct,
        },
        "trading_enabled": is_trading_enabled(),
        "last_cycle_at": format_vn_time(get_last_cycle_at()),
        "rsi_rev": donchian,
        "rate_limited": rate_limited,
        "rate_limit_remaining_sec": rate_limit_remaining_sec,
    }


@app.get("/api/donchian/trades")
def api_donchian_trades(
    status: str = Query(default="open"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=20, le=100),
) -> dict:
    kind = status.strip().lower()
    if kind not in {"open", "closed"}:
        kind = "open"
    return _donchian_trades_payload(status=kind, page=page, page_size=page_size)


@app.get("/api/rsi-rev/trades")
def api_rsi_rev_trades(
    status: str = Query(default="open"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=20, le=100),
) -> dict:
    """Alias kept for dashboard JS compatibility."""
    kind = status.strip().lower()
    if kind not in {"open", "closed"}:
        kind = "open"
    return _donchian_trades_payload(status=kind, page=page, page_size=page_size)


@app.get("/api/donchian/daily")
def api_donchian_daily(days: int = Query(default=30, ge=1, le=90)) -> dict:
    from src.donchian import store

    return {"days": days, "rows": store.daily_stats(days)}


@app.get("/api/rsi-rev/daily")
def api_rsi_rev_daily(days: int = Query(default=30, ge=1, le=90)) -> dict:
    """Alias kept for dashboard JS compatibility."""
    from src.donchian import store

    return {"days": days, "rows": store.daily_stats(days)}


@app.get("/api/equity-history")
def api_equity_history(range: str = Query(default="7d")) -> dict:
    return build_equity_history_payload(range)


@app.post("/settings/trading/start")
def form_start_trading() -> RedirectResponse:
    set_trading_enabled(True)
    return RedirectResponse(url="/", status_code=303)


@app.post("/settings/trading/stop")
def form_stop_trading() -> RedirectResponse:
    set_trading_enabled(False)
    return RedirectResponse(url="/", status_code=303)

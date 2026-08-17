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
)
from src.bot_state import (
    get_account_balance,
    get_last_cycle_at,
    is_trading_enabled,
    set_trading_enabled,
)
from src.ema_rsi.config import MAX_OPEN
from src.web.number_format import format_dashboard_pnl, format_dashboard_price
from src.web.time_format import format_vn_time

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["vn_time"] = format_vn_time
templates.env.filters["dash_price"] = format_dashboard_price
templates.env.filters["dash_pnl"] = format_dashboard_pnl

app = FastAPI(title=f"{EXCHANGE_DISPLAY_NAME} EMA RSI Bot Dashboard")

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


def _ema_rsi_trade_payload(limit: int = 50) -> dict:
    from src.ema_rsi import store
    from src.ema_rsi.trading import realized_pnl

    rows = store.list_trades(limit=limit)
    symbols = sorted({str(row["symbol"]) for row in rows if row["status"] == "open"})
    marks = _fetch_symbol_marks(symbols)
    trades = []
    for row in rows:
        symbol = str(row["symbol"])
        side = str(row["side"])
        entry = float(row["entry"] or 0)
        size = float(row["size"] or 0)
        status = str(row["status"])
        mark = marks.get(symbol, 0.0)
        unreal = None
        if status == "open" and mark > 0 and size > 0:
            unreal = realized_pnl(side, entry, mark, size)
        trades.append(
            {
                "id": int(row["id"]),
                "symbol": symbol,
                "side": side,
                "status": status,
                "entry": entry,
                "sl": float(row["sl"] or 0),
                "tp": float(row["tp"] or 0),
                "r": float(row["r"] or 0),
                "size": size,
                "margin_usdt": float(row["margin_usdt"] or 0),
                "close_price": row["close_price"],
                "close_reason": row["close_reason"] or "",
                "pnl_usdt": row["pnl_usdt"],
                "unrealized_pnl": unreal,
                "mark": mark,
                "opened_at": format_vn_time(str(row["opened_at"])) if row["opened_at"] else "",
                "closed_at": format_vn_time(str(row["closed_at"])) if row["closed_at"] else "",
            }
        )
    open_count = sum(1 for t in trades if t["status"] == "open")
    closed_realized = sum(
        float(t["pnl_usdt"] or 0) for t in trades if t["status"] == "closed" and t["pnl_usdt"] is not None
    )
    open_unrealized = sum(float(t["unrealized_pnl"] or 0) for t in trades if t["status"] == "open")
    return {
        "trades": trades,
        "open_count": open_count,
        "summary": {
            "open_unrealized": open_unrealized,
            "closed_realized": closed_realized,
            "total_pnl": open_unrealized + closed_realized,
        },
    }


def _dashboard_context() -> dict:
    account = get_account_balance()
    ema_rsi = _ema_rsi_trade_payload(50)
    return {
        "exchange_name": EXCHANGE_DISPLAY_NAME,
        "bot_title": "EMA RSI",
        "account": account,
        "last_cycle_at": get_last_cycle_at(),
        "trading_enabled": is_trading_enabled(),
        "ema_rsi": ema_rsi,
        "ema_rsi_max_open": MAX_OPEN,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", _dashboard_context())


@app.get("/api/status")
def api_status() -> dict:
    account = get_account_balance()
    ema_rsi = _ema_rsi_trade_payload(50)
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
        "ema_rsi": ema_rsi,
        "rate_limited": rate_limited,
        "rate_limit_remaining_sec": rate_limit_remaining_sec,
    }


@app.get("/api/ema-rsi/trades")
def api_ema_rsi_trades(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return _ema_rsi_trade_payload(limit)


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

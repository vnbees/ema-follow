from pathlib import Path
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
    MARGIN_COIN,
    MAX_OPEN_LEGS,
    MAX_OPEN_SYMBOLS,
    PAIR_PROFIT_TARGET_PCT,
    PROFIT_TARGET_PCT,
)
from src.rsi_signals import price_move_pct, should_take_profit
from src.bot_state import (
    get_account_balance,
    get_all_statuses,
    get_last_cycle_at,
    is_trading_enabled,
    set_trading_enabled,
    status_to_dict,
)
from src.margin_guard import get_margin_guard_state
from src.market_universe import get_last_refreshed, get_scan_stats
from src.orderflow import live_state_to_dict
from src.pnl import estimate_tp_pnl_usdt, leg_realized_pnl, leg_unrealized_pnl, roi_pct
from src.profit_target import reset_baseline_to_current_equity, trigger_manual_profit_take
from src.spot_transfer import (
    set_enabled as set_spot_transfer_enabled,
    set_transfer_pct,
    today_transfer_status,
)
from src.web.calendar_build import VN_TZ, build_rsi_pnl_calendar
from src.web.number_format import format_dashboard_pnl, format_dashboard_price, format_dashboard_size
from src.web.time_format import format_vn_time

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["vn_time"] = format_vn_time
templates.env.filters["dash_price"] = format_dashboard_price
templates.env.filters["dash_size"] = format_dashboard_size
templates.env.filters["dash_pnl"] = format_dashboard_pnl

app = FastAPI(title=f"{EXCHANGE_DISPLAY_NAME} RSI Bot Dashboard")

_PUBLIC_PATHS = frozenset({"/login"})
_SESSION_SECRET = DASHBOARD_SESSION_SECRET or secrets.token_urlsafe(48)


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


# Session must be outermost so request.session is available in auth middleware.
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


_EQUITY_RANGES = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


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


def build_spot_history_payload(range_key: str) -> dict:
    key = range_key if range_key in _EQUITY_RANGES else "7d"
    rows = db.get_spot_snapshots(_equity_since_for_range(key))
    return {
        "range": key,
        "margin_coin": MARGIN_COIN,
        "points": [
            {
                "time_vn": format_vn_time(str(row["recorded_at"])),
                "balance": float(row["balance"]),
            }
            for row in rows
        ],
    }


def _build_spot_transfer_rows(limit: int = 20) -> list[dict]:
    rows = db.get_spot_transfers(limit)
    return [
        {
            "time_vn": format_vn_time(str(row["created_at"])),
            "transfer_date": str(row["transfer_date"]),
            "amount": float(row["amount"]),
            "status": str(row["status"]),
            "legs_closed": int(row["legs_closed"] or 0),
            "tran_id": row["tran_id"] or "—",
            "error": row["error"] or "—",
            "spot_after": (
                float(row["spot_after"]) if row["spot_after"] is not None else None
            ),
        }
        for row in rows
    ]


def _fetch_symbol_marks(symbols: list[str]) -> dict[str, float]:
    """WS mark only — never REST premiumIndex on dashboard refresh."""
    result: dict[str, float] = {}
    try:
        from src.exchange.binance_ws import get_mark_from_ws
    except Exception:  # noqa: BLE001
        get_mark_from_ws = None  # type: ignore[assignment]
    for symbol in symbols:
        mark = 0.0
        if get_mark_from_ws is not None:
            try:
                cached = get_mark_from_ws(symbol)
                if cached is not None and cached > 0:
                    mark = float(cached)
            except Exception:  # noqa: BLE001
                mark = 0.0
        result[symbol] = mark
    return result


def _unrealized_map_from_groups(symbol_groups: list[dict]) -> dict[str, float]:
    """Estimate open unrealized from DB lots + mark (no positionRisk)."""
    result: dict[str, float] = {}
    for group in symbol_groups:
        long_pnl = group.get("agg_long", {}).get("pnl_usdt") or 0.0
        short_pnl = group.get("agg_short", {}).get("pnl_usdt") or 0.0
        result[group["symbol"]] = float(long_pnl) + float(short_pnl)
    return result


def _agg_side_from_open_lots(side: str, open_lots: list, mark: float) -> dict:
    """DB-weighted avg entry + size for one side; PnL from mark."""
    status_key = f"{side}_status"
    entry_key = f"{side}_entry"
    size_key = f"{side}_size"
    total_value = 0.0
    total_size = 0.0
    for row in open_lots:
        if row[status_key] != "open":
            continue
        size = float(row[size_key] or 0)
        entry = float(row[entry_key] or 0)
        if size <= 0 or entry <= 0:
            continue
        total_value += entry * size
        total_size += size
    avg_entry = (total_value / total_size) if total_size > 0 else 0.0
    return _build_agg_side(side, total_size, avg_entry, mark)


def _lot_is_open(lot) -> bool:
    return lot["long_status"] == "open" or lot["short_status"] == "open"


def _build_agg_side(
    side: str,
    size: float,
    avg_entry: float,
    mark: float,
    *,
    exchange_pnl: float | None = None,
) -> dict:
    if size <= 0 or mark <= 0:
        return {
            "size": size,
            "avg_entry": avg_entry,
            "move_pct": None,
            "pnl_usdt": None,
            "tp_ready": False,
        }
    move = price_move_pct(side, avg_entry, mark)
    if exchange_pnl is not None:
        pnl = exchange_pnl
    elif side == "long":
        pnl = (mark - avg_entry) * size
    else:
        pnl = (avg_entry - mark) * size
    return {
        "size": size,
        "avg_entry": avg_entry,
        "move_pct": move,
        "pnl_usdt": pnl,
        "tp_ready": should_take_profit(side, avg_entry, mark),
    }


def _lot_open_leg_count(lot) -> tuple[int, int]:
    open_legs = 0
    if lot["long_status"] == "open":
        open_legs += 1
    if lot["short_status"] == "open":
        open_legs += 1
    return open_legs, 2


def _resolve_closed_leg_pnl(side: str, lot) -> tuple[float | None, bool]:
    is_long = side == "long"
    entry = float(lot["long_entry"] if is_long else lot["short_entry"])
    size = float(lot["long_size"] if is_long else lot["short_size"])
    realized = lot["long_realized_pnl_usdt"] if is_long else lot["short_realized_pnl_usdt"]
    close_price = lot["long_close_price"] if is_long else lot["short_close_price"]
    if "long_close_price" not in lot.keys():
        close_price = None
    pnl = leg_realized_pnl(
        side,
        entry,
        size,
        realized_pnl_usdt=realized,
        close_price=close_price,
    )
    if pnl is not None:
        return pnl, False
    if entry > 0 and size > 0:
        estimated = estimate_tp_pnl_usdt(entry, size, PAIR_PROFIT_TARGET_PCT)
        if estimated is not None:
            return estimated, True
    return None, False


def _leg_fields(
    side: str,
    lot,
    mark: float,
    *,
    exchange_leg_pnl: float | None = None,
) -> dict:
    is_long = side == "long"
    status = lot["long_status"] if is_long else lot["short_status"]
    is_open = status == "open"
    entry = float(lot["long_entry"] if is_long else lot["short_entry"])
    size = float(lot["long_size"] if is_long else lot["short_size"])
    closed_at = lot["long_closed_at"] if is_long else lot["short_closed_at"]
    close_price_col = "long_close_price" if is_long else "short_close_price"
    close_price = None
    if close_price_col in lot.keys() and lot[close_price_col] is not None:
        close_price = float(lot[close_price_col])

    move_pct = None
    pnl = None
    pnl_estimated = False
    if is_open and mark > 0:
        move_pct = price_move_pct(side, entry, mark)
        if exchange_leg_pnl is not None:
            pnl = exchange_leg_pnl
        else:
            pnl = leg_unrealized_pnl(side, entry, size, mark)
    elif not is_open:
        if close_price and close_price > 0:
            move_pct = price_move_pct(side, entry, close_price)
        pnl, pnl_estimated = _resolve_closed_leg_pnl(side, lot)

    tp_ready = is_open and mark > 0 and should_take_profit(side, entry, mark)

    return {
        "side": side,
        "status": status,
        "is_open": is_open,
        "size": size,
        "entry": entry,
        "close_price": close_price,
        "move_pct": move_pct,
        "tp_ready": tp_ready,
        "pnl_usdt": pnl,
        "pnl_estimated": pnl_estimated,
        "closed_at": closed_at,
    }


def _build_lot_detail(
    lot,
    mark: float,
    fifo_index: int | None,
    fifo_count: int,
) -> dict:
    open_legs, total_legs = _lot_open_leg_count(lot)
    long_leg = _leg_fields("long", lot, mark)
    short_leg = _leg_fields("short", lot, mark)
    realized_total = 0.0
    has_realized = False
    for leg in (long_leg, short_leg):
        if not leg["is_open"] and leg["pnl_usdt"] is not None:
            realized_total += leg["pnl_usdt"]
            has_realized = True
    return {
        "lot_id": int(lot["id"]),
        "fifo_index": fifo_index,
        "fifo_count": fifo_count,
        "is_open": _lot_is_open(lot),
        "open_legs": open_legs,
        "total_legs": total_legs,
        "lot_state": f"{open_legs}/{total_legs} leg mở",
        "margin_usdt": float(lot["margin_usdt"] or 0),
        "entry_trigger": lot["entry_trigger"] or "—",
        "opened_at": lot["opened_at"],
        "realized_total_usdt": realized_total if has_realized else None,
        "long": long_leg,
        "short": short_leg,
    }


def build_symbol_groups(lots, statuses: dict, mark_map: dict[str, float]) -> list[dict]:
    """Build dashboard groups from DB lots + WS mark — no REST positionRisk."""
    by_symbol: dict[str, list] = {}
    for lot in lots:
        by_symbol.setdefault(lot["symbol"], []).append(lot)

    groups: list[dict] = []
    for symbol in sorted(by_symbol.keys()):
        symbol_lots = sorted(by_symbol[symbol], key=lambda r: r["opened_at"])
        mark = mark_map.get(symbol, 0.0)
        st = statuses.get(symbol)
        open_lots = [row for row in symbol_lots if _lot_is_open(row)]
        fifo_count = len(open_lots)
        agg_long = _agg_side_from_open_lots("long", open_lots, mark)
        agg_short = _agg_side_from_open_lots("short", open_lots, mark)

        lot_details: list[dict] = []
        fifo_n = 0
        for lot in symbol_lots:
            fifo_index = None
            if _lot_is_open(lot):
                fifo_n += 1
                fifo_index = fifo_n
            # Per-leg PnL from entry×mark (not exchange side share).
            lot_details.append(
                _build_lot_detail(lot, mark, fifo_index, fifo_count),
            )

        groups.append(
            {
                "symbol": symbol,
                "fifo_count": fifo_count,
                "rsi_live": st.rsi_value if st else 0.0,
                "mark_price": mark,
                "agg_long": agg_long,
                "agg_short": agg_short,
                "lots": lot_details,
            }
        )
    return groups


def flatten_open_legs(symbol_groups: list[dict]) -> list[dict]:
    """Flat list of open LONG/SHORT legs for dashboard leg-group view."""
    rows: list[dict] = []
    for group in symbol_groups:
        symbol = group["symbol"]
        mark = group.get("mark_price") or 0.0
        for lot in group.get("lots", []):
            if not lot.get("is_open"):
                continue
            for side in ("long", "short"):
                leg = lot.get(side) or {}
                if not leg.get("is_open"):
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "side": side,
                        "lot_id": lot["lot_id"],
                        "size": leg.get("size") or 0.0,
                        "entry": leg.get("entry") or 0.0,
                        "mark": mark,
                        "move_pct": leg.get("move_pct"),
                        "pnl_usdt": leg.get("pnl_usdt"),
                        "tp_ready": bool(leg.get("tp_ready")),
                        "entry_trigger": lot.get("entry_trigger") or "—",
                        "opened_at": lot.get("opened_at"),
                    }
                )
    return rows


def flatten_open_sides(symbol_groups: list[dict]) -> list[dict]:
    """One row per open symbol-side (DB avg) for position-group headers / sort."""
    rows: list[dict] = []
    for group in symbol_groups:
        symbol = group["symbol"]
        mark = group.get("mark_price") or 0.0
        for side, agg in (("long", group.get("agg_long") or {}), ("short", group.get("agg_short") or {})):
            size = float(agg.get("size") or 0)
            if size <= 0:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "entry": float(agg.get("avg_entry") or 0),
                    "mark": mark,
                    "move_pct": agg.get("move_pct"),
                    "pnl_usdt": agg.get("pnl_usdt"),
                    "tp_ready": bool(agg.get("tp_ready")),
                    "lots": [
                        lot
                        for lot in group.get("lots", [])
                        if lot.get("is_open") and (lot.get(side) or {}).get("is_open")
                    ],
                    "opened_at": min(
                        (
                            lot.get("opened_at") or ""
                            for lot in group.get("lots", [])
                            if lot.get("is_open") and (lot.get(side) or {}).get("is_open")
                        ),
                        default="",
                    ),
                }
            )
    return rows


def _build_pnl_summary(symbol_groups: list[dict], unrealized_map: dict[str, float]) -> dict:
    open_symbols = {
        g["symbol"] for g in symbol_groups if any(lot["is_open"] for lot in g["lots"])
    }
    open_unrealized = sum(unrealized_map.get(sym, 0.0) for sym in open_symbols)
    closed_realized, closed_with_pnl, closed_total_legs = db.get_closed_realized_from_lots()
    open_lot_count = sum(
        1 for g in symbol_groups for lot in g["lots"] if lot["is_open"]
    )
    return {
        "open_unrealized": open_unrealized,
        "open_count": open_lot_count,
        "open_symbols": len(open_symbols),
        "open_legs": db.count_open_legs(),
        "closed_realized": closed_realized,
        "closed_count": closed_total_legs,
        "closed_with_pnl": closed_with_pnl,
        "total_pnl": open_unrealized + closed_realized,
    }


def _resolve_calendar_month(year: int | None, month: int | None) -> tuple[int, int]:
    today = datetime.now(VN_TZ).date()
    y = year if year is not None else today.year
    m = month if month is not None else today.month
    if m < 1 or m > 12:
        return today.year, today.month
    if y < 2000 or y > 2100:
        return today.year, today.month
    return y, m


def _lot_to_calendar_event(event) -> dict:
    margin = float(event["margin_usdt"] or 0)
    entry = float(event["entry"] or 0)
    size = float(event["size"] or 0)
    side = event["side"]
    pnl = leg_realized_pnl(
        side,
        entry,
        size,
        realized_pnl_usdt=event["realized_pnl_usdt"],
        close_price=event["close_price"],
    )
    pnl_estimated = False
    if pnl is None and entry > 0 and size > 0:
        estimated = estimate_tp_pnl_usdt(entry, size, PAIR_PROFIT_TARGET_PCT)
        if estimated is not None:
            pnl = estimated
            pnl_estimated = True
    close_price = float(event["close_price"]) if event["close_price"] is not None else None
    move_pct = price_move_pct(side, entry, close_price) if close_price and entry > 0 else None
    if move_pct is None and pnl_estimated:
        move_pct = PAIR_PROFIT_TARGET_PCT
    return {
        "symbol": event["symbol"],
        "side": side,
        "lot_id": int(event["lot_id"]),
        "entry": entry,
        "size": size,
        "close_price": close_price,
        "move_pct": move_pct,
        "entry_trigger": event.get("entry_trigger") or "—",
        "close_label": f"Chốt ≥{PAIR_PROFIT_TARGET_PCT:g}%",
        "closed_at": str(event["closed_at"]),
        "closed_at_vn": format_vn_time(str(event["closed_at"])),
        "realized_pnl_usdt": pnl,
        "pnl_estimated": pnl_estimated,
        "roi_pct": roi_pct(pnl, margin),
        "margin_usdt": margin,
        "dca_count": 0,
        "vn_date": event.get("vn_date", ""),
    }


def _build_pnl_calendar_payload(year: int, month: int) -> dict:
    events = db.get_closed_lot_side_events()
    calendar = build_rsi_pnl_calendar(year, month, events)
    calendar["month_events"] = [
        _lot_to_calendar_event(event) for event in calendar["month_events"]
    ]
    for week in calendar["weeks"]:
        for cell in week:
            if cell.get("events"):
                cell["events"] = [_lot_to_calendar_event(event) for event in cell["events"]]
    return calendar


def _build_recent_orders(limit: int = 10) -> list[dict]:
    rows = db.get_recent_leg_events(limit)
    orders: list[dict] = []
    for row in rows:
        event_type = str(row["event_type"])
        side = str(row["side"])
        entry = float(row["entry"] or 0)
        size = float(row["size"] or 0)
        pnl = None
        if event_type == "close":
            pnl = leg_realized_pnl(
                side,
                entry,
                size,
                realized_pnl_usdt=row["realized_pnl_usdt"],
                close_price=row["close_price"],
            )
        orders.append(
            {
                "time_vn": format_vn_time(str(row["event_at"])),
                "action": "Mở" if event_type == "open" else "Đóng",
                "symbol": str(row["symbol"]),
                "side": side.upper(),
                "size": size,
                "entry": entry,
                "pnl": pnl,
                "trigger": row["entry_trigger"] or "—",
                "lot_id": int(row["lot_id"]),
            }
        )
    return orders


def _simple_dashboard_context() -> dict:
    account = get_account_balance()
    spot_status = today_transfer_status()
    return {
        "exchange_name": EXCHANGE_DISPLAY_NAME,
        "account": account,
        "recent_orders": _build_recent_orders(10),
        "last_cycle_at": get_last_cycle_at(),
        "trading_enabled": is_trading_enabled(),
        "spot_transfer": spot_status,
        "spot_transfers": _build_spot_transfer_rows(15),
        "margin_coin": MARGIN_COIN,
    }


def _dashboard_context(
    year: int | None,
    month: int | None,
    day: int | None,
) -> dict:
    lots = db.get_pair_lots_for_dashboard(limit=200)
    statuses = get_all_statuses()
    symbols = sorted({lot["symbol"] for lot in lots})
    mark_map = _fetch_symbol_marks(symbols)
    symbol_groups = build_symbol_groups(lots, statuses, mark_map)
    unrealized_map = _unrealized_map_from_groups(symbol_groups)
    pnl_summary = _build_pnl_summary(symbol_groups, unrealized_map)
    open_count = pnl_summary["open_count"]
    closed_count = len(lots) - open_count

    checked, last_signal = get_scan_stats()
    cal_year, cal_month = _resolve_calendar_month(year, month)
    pnl_calendar = _build_pnl_calendar_payload(cal_year, cal_month)
    selected_day = day if day is not None and 1 <= day <= 31 else None
    account = get_account_balance()
    margin_guard = get_margin_guard_state()
    effective_tp = (
        margin_guard.effective_tp_pct
        if margin_guard.effective_tp_pct
        else PAIR_PROFIT_TARGET_PCT
    )

    return {
        "exchange_name": EXCHANGE_DISPLAY_NAME,
        "symbol_groups": symbol_groups,
        "max_open_symbols": MAX_OPEN_SYMBOLS,
        "max_open_legs": MAX_OPEN_LEGS,
        "profit_target_pct": effective_tp,
        "base_profit_target_pct": PAIR_PROFIT_TARGET_PCT,
        "pnl_summary": pnl_summary,
        "open_count": open_count,
        "closed_count": closed_count,
        "last_refreshed": get_last_refreshed(),
        "last_scan_checked": checked,
        "last_signal_symbol": last_signal,
        "pnl_calendar": pnl_calendar,
        "selected_day": selected_day,
        "cal_year": cal_year,
        "cal_month": cal_month,
        "account": account,
        "margin_guard": margin_guard,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        _simple_dashboard_context(),
    )


@app.get("/api/pnl-calendar")
def api_pnl_calendar(
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
) -> dict:
    cal_year, cal_month = _resolve_calendar_month(year, month)
    return _build_pnl_calendar_payload(cal_year, cal_month)


@app.get("/api/ofi")
def api_ofi() -> dict:
    data = live_state_to_dict()
    data["last_updated"] = format_vn_time(data.get("last_updated"))
    for row in data.get("recent_predictions", []):
        row["time"] = format_vn_time(row.get("time"))
    return data


@app.get("/api/profit-takes")
def api_profit_takes() -> list[dict]:
    rows = db.get_profit_takes(limit=100)
    return [
        {
            "id": row["id"],
            "taken_at": format_vn_time(str(row["taken_at"])),
            "baseline_before": float(row["baseline_before"]),
            "equity_after": float(row["equity_after"]),
            "pnl_usdt": float(row["pnl_usdt"]),
            "pnl_pct": float(row["pnl_pct"]),
            "trigger_type": (
                str(row["trigger_type"])
                if "trigger_type" in row.keys() and row["trigger_type"]
                else "target"
            ),
        }
        for row in rows
    ]


@app.get("/api/status")
def api_status() -> dict:
    account = get_account_balance()
    statuses = get_all_statuses()
    ctx = _dashboard_context(None, None, None)
    symbol_groups = ctx["symbol_groups"]
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
            "baseline_equity": account.baseline_equity,
            "pnl_usdt": account.pnl_usdt,
            "pnl_pct": account.pnl_pct,
            "profit_target_pct": account.profit_target_pct or PROFIT_TARGET_PCT,
            "baseline_updated_at": format_vn_time(account.baseline_updated_at),
        },
        "pnl_summary": ctx["pnl_summary"],
        "symbol_groups": symbol_groups,
        "open_sides": flatten_open_sides(symbol_groups),
        "open_legs": flatten_open_legs(symbol_groups),
        "profit_target_pct": PAIR_PROFIT_TARGET_PCT,
        "trading_enabled": is_trading_enabled(),
        "rate_limited": rate_limited,
        "rate_limit_remaining_sec": rate_limit_remaining_sec,
        "symbols": {
            sym: {
                **status_to_dict(st),
                "last_updated": format_vn_time(st.last_updated),
            }
            for sym, st in statuses.items()
        },
    }


@app.get("/api/positions")
def api_positions() -> dict:
    """Lightweight positions payload for dashboard (DB + WS mark, no REST)."""
    lots = db.get_pair_lots_for_dashboard(limit=200)
    statuses = get_all_statuses()
    symbols = sorted({lot["symbol"] for lot in lots})
    mark_map = _fetch_symbol_marks(symbols)
    symbol_groups = build_symbol_groups(lots, statuses, mark_map)
    rate_limited = False
    rate_limit_remaining_sec = 0.0
    try:
        from src.exchange import binance as binance_mod

        rate_limited = binance_mod.is_rate_limited()
        rate_limit_remaining_sec = binance_mod.rate_limit_remaining_sec()
    except Exception:  # noqa: BLE001
        pass
    return {
        "symbol_groups": symbol_groups,
        "open_sides": flatten_open_sides(symbol_groups),
        "open_legs": flatten_open_legs(symbol_groups),
        "pnl_summary": _build_pnl_summary(
            symbol_groups, _unrealized_map_from_groups(symbol_groups),
        ),
        "profit_target_pct": PAIR_PROFIT_TARGET_PCT,
        "trading_enabled": is_trading_enabled(),
        "rate_limited": rate_limited,
        "rate_limit_remaining_sec": rate_limit_remaining_sec,
    }


@app.post("/api/positions/close-side")
async def api_close_side(request: Request) -> JSONResponse:
    from src.rsi_trading import manual_close_side

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "message": "invalid JSON"}, status_code=400)
    symbol = str(body.get("symbol") or "").strip()
    side = str(body.get("side") or "").strip().lower()
    if not symbol or side not in ("long", "short"):
        return JSONResponse(
            {"ok": False, "message": "symbol and side (long|short) required"},
            status_code=400,
        )
    result = manual_close_side(symbol, side)
    status = 200 if result.get("ok") else (429 if result.get("rate_limited") else 400)
    return JSONResponse(result, status_code=status)


@app.post("/api/positions/close-leg")
async def api_close_leg(request: Request) -> JSONResponse:
    from src.rsi_trading import manual_close_leg

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "message": "invalid JSON"}, status_code=400)
    try:
        lot_id = int(body.get("lot_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "message": "lot_id required"}, status_code=400)
    side = str(body.get("side") or "").strip().lower()
    if side not in ("long", "short"):
        return JSONResponse(
            {"ok": False, "message": "side must be long or short"},
            status_code=400,
        )
    result = manual_close_leg(lot_id, side)
    status = 200 if result.get("ok") else (429 if result.get("rate_limited") else 400)
    return JSONResponse(result, status_code=status)


@app.get("/api/equity-history")
def api_equity_history(range: str = Query(default="7d")) -> dict:
    return build_equity_history_payload(range)


@app.get("/api/spot-history")
def api_spot_history(range: str = Query(default="7d")) -> dict:
    return build_spot_history_payload(range)


@app.get("/api/spot-transfers")
def api_spot_transfers(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    return _build_spot_transfer_rows(limit)


@app.post("/settings/spot-transfer")
def form_spot_transfer_settings(
    enabled: str = Form(default="off"),
    pct: float = Form(default=1.0),
) -> RedirectResponse:
    set_spot_transfer_enabled(enabled.lower() in ("1", "true", "yes", "on"))
    if pct > 0:
        set_transfer_pct(pct)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/symbols")
def api_symbols() -> list[str]:
    return sorted({row["symbol"] for row in db.get_all_open_pair_lots()})


@app.post("/settings/trading/start")
def form_start_trading() -> RedirectResponse:
    set_trading_enabled(True)
    return RedirectResponse(url="/", status_code=303)


@app.post("/settings/trading/stop")
def form_stop_trading() -> RedirectResponse:
    set_trading_enabled(False)
    return RedirectResponse(url="/", status_code=303)


@app.post("/settings/profit-take/trigger")
def form_trigger_profit_take() -> RedirectResponse:
    trigger_manual_profit_take()
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/profit-take/trigger")
def api_trigger_profit_take() -> dict:
    return trigger_manual_profit_take()


@app.post("/settings/baseline/reset")
def form_reset_baseline() -> RedirectResponse:
    reset_baseline_to_current_equity()
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/baseline/reset")
def api_reset_baseline() -> dict:
    baseline = reset_baseline_to_current_equity()
    if baseline is None:
        return {"ok": False, "error": "could not fetch equity"}
    return {
        "ok": True,
        "baseline_equity": baseline,
        "baseline_updated_at": format_vn_time(db.get_baseline_updated_at()),
    }

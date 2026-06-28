from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src import database as db
from src.bitget_client import (
    BitgetClientError,
    fetch_side_mark_price,
    fetch_side_unrealized_pnl,
    fetch_symbol_positions,
    has_credentials,
)
from src.config import MAX_OPEN_LEGS, MAX_OPEN_SYMBOLS, PAIR_PROFIT_TARGET_PCT, PROFIT_TARGET_PCT
from src.rsi_signals import price_move_pct, should_take_profit
from src.bot_state import (
    get_account_balance,
    get_all_statuses,
    status_to_dict,
)
from src.market_universe import get_last_refreshed, get_scan_stats
from src.orderflow import live_state_to_dict
from src.pnl import estimate_tp_pnl_usdt, leg_realized_pnl, leg_unrealized_pnl, roi_pct
from src.profit_target import reset_baseline_to_current_equity, trigger_manual_profit_take
from src.web.calendar_build import VN_TZ, build_rsi_pnl_calendar
from src.web.time_format import format_vn_time

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["vn_time"] = format_vn_time

app = FastAPI(title="Bitget RSI Bot Dashboard")


def _fetch_symbol_marks(symbols: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    if not has_credentials():
        return result
    for symbol in symbols:
        try:
            result[symbol] = fetch_side_mark_price(symbol)
        except BitgetClientError:
            result[symbol] = 0.0
    return result


def _fetch_open_unrealized_by_symbol(symbols: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    if not has_credentials():
        return result
    for symbol in symbols:
        try:
            long_pnl = fetch_side_unrealized_pnl(symbol, "long")
            short_pnl = fetch_side_unrealized_pnl(symbol, "short")
            result[symbol] = long_pnl + short_pnl
        except BitgetClientError:
            result[symbol] = 0.0
    return result


def _lot_is_open(lot) -> bool:
    return lot["long_status"] == "open" or lot["short_status"] == "open"


def _build_agg_side(side: str, size: float, avg_entry: float, mark: float) -> dict:
    if size <= 0 or mark <= 0:
        return {
            "size": size,
            "avg_entry": avg_entry,
            "move_pct": None,
            "pnl_usdt": None,
            "tp_ready": False,
        }
    move = price_move_pct(side, avg_entry, mark)
    if side == "long":
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


def _build_lot_detail(lot, mark: float, fifo_index: int | None, fifo_count: int) -> dict:
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


def _fetch_agg_for_symbol(symbol: str, mark: float) -> tuple[dict, dict]:
    if not has_credentials():
        return (
            _build_agg_side("long", 0, 0, mark),
            _build_agg_side("short", 0, 0, mark),
        )
    try:
        positions = fetch_symbol_positions(symbol)
        return (
            _build_agg_side("long", positions["long"].size, positions["long"].avg_price, mark),
            _build_agg_side("short", positions["short"].size, positions["short"].avg_price, mark),
        )
    except BitgetClientError:
        return (
            _build_agg_side("long", 0, 0, mark),
            _build_agg_side("short", 0, 0, mark),
        )


def build_symbol_groups(lots, statuses: dict, mark_map: dict[str, float]) -> list[dict]:
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
        agg_long, agg_short = _fetch_agg_for_symbol(symbol, mark)

        lot_details: list[dict] = []
        fifo_n = 0
        for lot in symbol_lots:
            fifo_index = None
            if _lot_is_open(lot):
                fifo_n += 1
                fifo_index = fifo_n
            lot_details.append(_build_lot_detail(lot, mark, fifo_index, fifo_count))

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


def _dashboard_context(
    year: int | None,
    month: int | None,
    day: int | None,
) -> dict:
    lots = db.get_pair_lots_for_dashboard(limit=200)
    statuses = get_all_statuses()
    symbols = sorted({lot["symbol"] for lot in lots})
    mark_map = _fetch_symbol_marks(symbols)
    unrealized_map = _fetch_open_unrealized_by_symbol(symbols)
    symbol_groups = build_symbol_groups(lots, statuses, mark_map)
    pnl_summary = _build_pnl_summary(symbol_groups, unrealized_map)
    open_count = pnl_summary["open_count"]
    closed_count = len(lots) - open_count

    checked, last_signal = get_scan_stats()
    cal_year, cal_month = _resolve_calendar_month(year, month)
    pnl_calendar = _build_pnl_calendar_payload(cal_year, cal_month)
    selected_day = day if day is not None and 1 <= day <= 31 else None

    return {
        "symbol_groups": symbol_groups,
        "max_open_symbols": MAX_OPEN_SYMBOLS,
        "max_open_legs": MAX_OPEN_LEGS,
        "profit_target_pct": PAIR_PROFIT_TARGET_PCT,
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
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
    day: int | None = Query(default=None),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        _dashboard_context(year, month, day),
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
        "symbol_groups": ctx["symbol_groups"],
        "profit_target_pct": PAIR_PROFIT_TARGET_PCT,
        "symbols": {
            sym: {
                **status_to_dict(st),
                "last_updated": format_vn_time(st.last_updated),
            }
            for sym, st in statuses.items()
        },
    }


@app.get("/api/symbols")
def api_symbols() -> list[str]:
    return sorted({row["symbol"] for row in db.get_all_open_pair_lots()})


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

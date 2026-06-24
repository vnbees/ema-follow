from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src import database as db
from src.bot_state import (
    get_account_balance,
    get_all_statuses,
    remove_symbol_status,
    status_to_dict,
)
from src.config import LEVERAGE, MARGIN_MODE, OFI_SYMBOL, PROFIT_TARGET_PCT
from src.orderflow import live_state_to_dict
from src.profit_target import reset_baseline_to_current_equity, trigger_manual_profit_take
from src.trading import on_symbol_added, on_symbol_removed

from src.web.calendar_build import build_profit_calendar
from src.web.time_format import format_vn_time

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["vn_time"] = format_vn_time

app = FastAPI(title="Bitget EMA Bot Dashboard")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    symbols = db.get_symbols()
    statuses = get_all_statuses()
    account = get_account_balance()
    symbol_statuses = [statuses[s] for s in symbols if s in statuses]

    today_vn = datetime.now(VN_TZ).date()
    try:
        cal_year = int(request.query_params.get("year", today_vn.year))
        cal_month = int(request.query_params.get("month", today_vn.month))
        if not (1 <= cal_month <= 12):
            raise ValueError("invalid month")
    except (TypeError, ValueError):
        cal_year, cal_month = today_vn.year, today_vn.month

    profit_takes = db.get_profit_takes(limit=200)
    profit_calendar = build_profit_calendar(cal_year, cal_month, profit_takes)
    for event in profit_calendar["month_events"]:
        event["taken_at_vn"] = format_vn_time(str(event["taken_at"]))
        trigger = str(event.get("trigger_type") or "target")
        event["trigger_label"] = "Tự động" if trigger == "target" else "Thủ công"

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "symbols": symbols,
            "symbol_statuses": symbol_statuses,
            "account": account,
            "margin_mode": MARGIN_MODE,
            "leverage": LEVERAGE,
            "profit_target_pct": PROFIT_TARGET_PCT,
            "profit_calendar": profit_calendar,
            "ofi_symbol": OFI_SYMBOL,
        },
    )


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
    return db.get_symbols()


@app.post("/api/symbols")
def api_add_symbol(payload: dict) -> dict:
    symbol = str(payload.get("symbol", "")).upper().strip()
    if not symbol:
        return {"ok": False, "error": "symbol required"}
    added = db.add_symbol(symbol)
    if added:
        on_symbol_added(symbol)
    return {"ok": True, "added": added, "symbols": db.get_symbols()}


@app.delete("/api/symbols/{symbol}")
def api_remove_symbol(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    removed = db.remove_symbol(symbol)
    if removed:
        on_symbol_removed(symbol)
        remove_symbol_status(symbol)
    return {"ok": True, "removed": removed, "symbols": db.get_symbols()}


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


@app.post("/settings/symbols/add")
def form_add_symbol(symbol: str = Form(...)) -> RedirectResponse:
    symbol = symbol.upper().strip()
    if symbol and db.add_symbol(symbol):
        on_symbol_added(symbol)
    return RedirectResponse(url="/", status_code=303)


@app.post("/settings/symbols/remove")
def form_remove_symbol(symbol: str = Form(...)) -> RedirectResponse:
    symbol = symbol.upper().strip()
    if symbol and db.remove_symbol(symbol):
        on_symbol_removed(symbol)
        remove_symbol_status(symbol)
    return RedirectResponse(url="/", status_code=303)

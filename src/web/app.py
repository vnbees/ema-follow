from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src import database as db
from src.bot_state import (
    get_account_balance,
    get_all_statuses,
    status_to_dict,
)
from src.config import PROFIT_TARGET_PCT
from src.market_universe import get_last_refreshed, get_scan_stats
from src.orderflow import live_state_to_dict
from src.profit_target import reset_baseline_to_current_equity, trigger_manual_profit_take

from src.web.time_format import format_vn_time

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["vn_time"] = format_vn_time

app = FastAPI(title="Bitget RSI Bot Dashboard")

_STATUS_LABELS = {
    "open": ("Đang mở", "badge-ok"),
    "rsi_cross_75": ("Thoát RSI 75", "badge-tp2"),
    "rsi_cross_25": ("Thoát RSI 25", "badge-sl"),
    "exchange_closed": ("Đóng (sàn)", "badge-closed"),
    "closed": ("Đã đóng", "badge-closed"),
}


def _trade_status(trade) -> tuple[str, str]:
    if trade["status"] == "open":
        return _STATUS_LABELS["open"]
    reason = str(trade["close_reason"] or "closed")
    return _STATUS_LABELS.get(reason, _STATUS_LABELS["closed"])


def _build_trade_row(trade, statuses: dict) -> dict:
    symbol = trade["symbol"]
    st = statuses.get(symbol)
    is_open = trade["status"] == "open"
    status_label, status_class = _trade_status(trade)
    rsi_live = st.rsi_value if st else 0.0
    dca_count = int(trade["dca_count"]) if "dca_count" in trade.keys() and trade["dca_count"] else 0
    return {
        "symbol": symbol,
        "side": trade["side"],
        "entry": float(trade["entry_price"]),
        "rsi_entry": float(trade["rsi_entry"] or 0),
        "rsi_live": rsi_live,
        "dca_count": dca_count,
        "entry_trigger": trade["entry_trigger"] or "—",
        "position_size": float(st.position_size)
        if is_open and st and st.position_size
        else float(trade["position_size"] or 0),
        "on_exchange": (st.on_exchange if st else False) if is_open else False,
        "is_open": is_open,
        "status_label": status_label,
        "status_class": status_class,
        "close_reason": trade["close_reason"] or "",
        "last_updated": st.last_updated if is_open and st else trade["updated_at"],
        "opened_at": trade["opened_at"],
        "closed_at": trade["closed_at"] if not is_open else "",
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    trades = db.get_rsi_trades_for_dashboard(closed_limit=50)
    statuses = get_all_statuses()
    trade_rows = [_build_trade_row(trade, statuses) for trade in trades]
    open_count = sum(1 for row in trade_rows if row["is_open"])
    closed_count = len(trade_rows) - open_count

    checked, last_signal = get_scan_stats()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "trade_rows": trade_rows,
            "open_count": open_count,
            "closed_count": closed_count,
            "last_refreshed": get_last_refreshed(),
            "last_scan_checked": checked,
            "last_signal_symbol": last_signal,
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
        "open_trades": [
            {
                "symbol": row["symbol"],
                "side": row["side"],
                "status": row["status"],
                "close_reason": row["close_reason"],
                "entry_price": float(row["entry_price"]),
                "rsi_entry": float(row["rsi_entry"] or 0),
                "dca_count": int(row["dca_count"]) if "dca_count" in row.keys() and row["dca_count"] else 0,
                "entry_trigger": row["entry_trigger"],
                "position_size": float(row["position_size"] or 0),
                "opened_at": format_vn_time(str(row["opened_at"])),
                "closed_at": format_vn_time(str(row["closed_at"])) if row["closed_at"] else None,
                "updated_at": format_vn_time(str(row["updated_at"])),
                "live": status_to_dict(statuses[row["symbol"]])
                if row["status"] == "open" and row["symbol"] in statuses
                else None,
            }
            for row in db.get_rsi_trades_for_dashboard(closed_limit=50)
        ],
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
    return [row["symbol"] for row in db.get_open_rsi_trades()]


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

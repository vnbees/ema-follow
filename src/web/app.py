from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src import database as db
from src.bot_state import get_status
from src.trading import on_symbol_changed

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Bitget EMA Bot Dashboard")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    status = get_status()
    cycles = db.get_all_trade_cycles(limit=50)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "status": status,
            "cycles": cycles,
            "symbol": db.get_symbol(),
        },
    )


@app.get("/api/status")
def api_status() -> dict:
    status = get_status()
    return {
        "symbol": status.symbol,
        "trend": status.trend,
        "candle_color": status.candle_color,
        "ema20": status.ema20,
        "ema50": status.ema50,
        "ema100": status.ema100,
        "ema200": status.ema200,
        "last_close": status.last_close,
        "position_side": status.position_side,
        "position_size": status.position_size,
        "avg_entry": status.avg_entry,
        "pending_orders": status.pending_orders,
        "margin_mode": status.margin_mode,
        "leverage": status.leverage,
        "balance_available": status.balance_available,
        "balance_equity": status.balance_equity,
        "last_updated": status.last_updated,
    }


@app.get("/api/cycles")
def api_cycles() -> list[dict]:
    return [dict(row) for row in db.get_all_trade_cycles(limit=100)]


@app.post("/api/settings/symbol")
def api_set_symbol(payload: dict) -> dict:
    symbol = str(payload.get("symbol", "")).upper().strip()
    if not symbol:
        return {"ok": False, "error": "symbol required"}
    db.set_setting("symbol", symbol)
    on_symbol_changed(symbol)
    return {"ok": True, "symbol": symbol}


@app.post("/settings/symbol")
def form_set_symbol(symbol: str = Form(...)) -> RedirectResponse:
    symbol = symbol.upper().strip()
    if symbol:
        db.set_setting("symbol", symbol)
        on_symbol_changed(symbol)
    return RedirectResponse(url="/", status_code=303)

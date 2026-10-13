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
    "all": timedelta(days=400),
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
    # DB-only: do not call Binance here (dashboard refresh must not burn REST weight).
    skim_events = _successful_skim_events()
    points = []
    for row in rows:
        recorded = str(row["recorded_at"])
        equity = float(row["equity"])
        signed = _skim_cumsum_at(skim_events, recorded)
        points.append(
            {
                "time_vn": format_vn_time(recorded),
                "equity": equity,
                "available": float(row["available"]),
                "total_wealth": equity + signed,
                "skim_to_date": signed,
            }
        )
    return {
        "range": key,
        "margin_coin": "USDT",
        "baseline_equity": db.get_baseline_equity(),
        "points": points,
        "daily": build_wealth_daily_payload(key, book="history")["daily"],
    }


def _successful_skim_events() -> list[tuple[datetime, float, bool]]:
    """(time, signed amount, is_manual).

    Bot skim is positive. A manual spot→futures return is negative so it is
    not booked as profit or as another withdrawal.
    """
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT created_at, amount, reason FROM spot_transfers
            WHERE status = 'success'
            ORDER BY created_at ASC
            """
        ).fetchall()
    out: list[tuple[datetime, float, bool]] = []
    for row in rows:
        raw = str(row["created_at"] or "")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        amount = float(row["amount"] or 0)
        manual = str(row["reason"] or "") == "return_to_futures"
        if manual:
            amount = -amount
        out.append((dt, amount, manual))
    return out


def _skim_cumsum_at(events: list[tuple[datetime, float]], recorded_at: str) -> float:
    try:
        ts = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    total = 0.0
    for dt, amt, *_manual in events:
        if dt <= ts:
            total += amt
        else:
            break
    return total


def build_wealth_daily_payload(range_key: str, *, book: str = "history") -> dict:
    """EOD futures equity + cumulative successful skim (total wealth), VN calendar days."""
    from zoneinfo import ZoneInfo

    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    key = range_key if range_key in _EQUITY_RANGES else "7d"
    since = _equity_since_for_range(key)
    # Need skim history before `since` so cum total is correct on day 1 of the window.
    snaps = db.get_equity_snapshots(None)
    skim_events = _successful_skim_events()
    fresh = None
    if book == "fresh":
        from src.web.book import book_epoch

        fresh = book_epoch()
        if fresh is not None:
            epoch_dt = datetime.fromisoformat(fresh["at"].replace("Z", "+00:00"))
            if epoch_dt.tzinfo is None:
                epoch_dt = epoch_dt.replace(tzinfo=timezone.utc)

            def _stamp(raw: str) -> datetime:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt

            snaps = [row for row in snaps if _stamp(str(row["recorded_at"])) >= epoch_dt]
            skim_events = [ev for ev in skim_events if ev[0] >= epoch_dt]

    eod: dict = {}
    for row in snaps:
        raw = str(row["recorded_at"])
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        day = dt.astimezone(vn).date()
        eod[day] = (float(row["equity"]), dt, raw)

    skim_by_day: dict = {}
    manual_by_day: dict = {}
    for dt, amt, is_manual in skim_events:
        day = dt.astimezone(vn).date()
        if is_manual:
            manual_by_day[day] = manual_by_day.get(day, 0.0) + (-amt)
        else:
            skim_by_day[day] = skim_by_day.get(day, 0.0) + amt

    days = sorted(eod)
    if key != "all":
        since_day = since.astimezone(vn).date()
        # Keep one day before window so first delta is meaningful; filter display after.
        days = [d for d in days if d >= since_day - timedelta(days=1)]

    daily = []
    cum_skim = 0.0
    cum_manual = 0.0
    # Seed with transfers before the first displayed day.
    if days:
        first_day = days[0]
        for dt, amt, is_manual in skim_events:
            if dt.astimezone(vn).date() < first_day:
                cum_skim += amt
                if is_manual:
                    cum_manual += -amt
            else:
                break

    prev_fut = prev_tot = None
    for day in days:
        fut, _snap_dt, _raw = eod[day]
        day_skim = float(skim_by_day.get(day, 0.0))
        day_manual = float(manual_by_day.get(day, 0.0))
        cum_skim += day_skim - day_manual
        cum_manual += day_manual
        total = fut + cum_skim
        if key != "all" and day < since.astimezone(vn).date():
            prev_fut, prev_tot = fut, total
            continue
        d_fut = None if prev_fut is None else fut - prev_fut
        d_tot = None if prev_tot is None else total - prev_tot
        daily.append(
            {
                "date": day.isoformat(),
                "futures": round(fut, 4),
                "total": round(total, 4),
                "skim_day": round(day_skim, 4),
                "skim_cum": round(max(0.0, cum_skim), 4),
                "manual_day": round(day_manual, 4),
                "manual_cum": round(cum_manual, 4),
                "d_futures": None if d_fut is None else round(d_fut, 4),
                "d_total": None if d_tot is None else round(d_tot, 4),
            }
        )
        prev_fut, prev_tot = fut, total

    baseline = db.get_baseline_equity()
    if fresh is not None:
        from src.web.book import ANCHOR

        baseline = ANCHOR
    return {
        "range": key,
        "baseline_equity": baseline,
        "daily": daily,
        "book": book,
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
    entry_rule = "standard"
    try:
        if "entry_rule" in row.keys() and row["entry_rule"]:
            entry_rule = str(row["entry_rule"])
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
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
        "entry_rule": entry_rule,
        "entry_rule_label": (
            "Quy tắc ngược" if entry_rule == "stale_opposite"
            else "Lệnh 2" if entry_rule == "favorable_add"
            else "Limit 2 chiều" if entry_rule == "dual_limit"
            else ""
        ),
    }


def _donchian_summary(since: str | None = None) -> dict:
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
    closed_realized = store.sum_closed_pnl(since)
    return {
        "open_count": store.count_open(),
        "closed_count": store.count_closed(since),
        "pending_count": 0,
        "summary": {
            "open_unrealized": open_unrealized,
            "closed_realized": closed_realized,
            "total_pnl": open_unrealized + closed_realized,
        },
        "marks": marks,
        "now_ts": now_ts,
    }


def _closed_pack(pnls: list[float]) -> dict:
    n = len(pnls)
    wins = [v for v in pnls if v > 0]
    losses = [v for v in pnls if v <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "n": n,
        "wr": (len(wins) / n * 100) if n else 0.0,
        "pf": (gross_win / gross_loss) if gross_loss > 0 else None,
        "pnl": sum(pnls),
    }


def _rule_report(marks: dict[str, float], since: str | None = None) -> dict:
    from src.donchian import store
    from src.donchian.trading import realized_pnl

    def rule_of(row) -> str:
        try:
            if "entry_rule" in row.keys() and row["entry_rule"]:
                return str(row["entry_rule"])
        except (KeyError, IndexError, TypeError, AttributeError):
            pass
        return "standard"

    stale_closed: list[float] = []
    standard_closed: list[float] = []
    stale_open_n = 0
    stale_open_upnl = 0.0
    recent: list[dict] = []
    for row in store.list_all_lots():
        rule = rule_of(row)
        status = str(row["status"])
        if rule != "stale_opposite":
            if status == "closed" and row["pnl_usdt"] is not None:
                if since and str(row["closed_at"] or "") < since:
                    continue
                standard_closed.append(float(row["pnl_usdt"]))
            continue
        if status == "closed" and row["pnl_usdt"] is not None:
            if since and str(row["closed_at"] or "") < since:
                continue
            pnl = float(row["pnl_usdt"])
            stale_closed.append(pnl)
            recent.append(
                {
                    "symbol": str(row["symbol"]),
                    "side": str(row["side"]),
                    "status": "closed",
                    "pnl": pnl,
                    "opened_at": format_vn_time(str(row["opened_at"] or "")),
                }
            )
        elif status == "open":
            stale_open_n += 1
            mark = marks.get(str(row["symbol"]), 0.0)
            upnl = None
            if mark > 0:
                upnl = realized_pnl(
                    str(row["side"]),
                    float(row["entry_px"] or 0),
                    mark,
                    float(row["size"] or 0),
                )
                stale_open_upnl += upnl
            recent.append(
                {
                    "symbol": str(row["symbol"]),
                    "side": str(row["side"]),
                    "status": "open",
                    "pnl": upnl,
                    "opened_at": format_vn_time(str(row["opened_at"] or "")),
                }
            )
    recent = recent[:8]
    return {
        "stale": {**_closed_pack(stale_closed), "open_n": stale_open_n, "open_upnl": stale_open_upnl},
        "standard": _closed_pack(standard_closed),
        "recent": recent,
    }


def _donchian_trades_payload(
    *, status: str, page: int, page_size: int, since: str | None = None
) -> dict:
    from src.donchian import store

    rows, total = store.list_lots_paged(
        status=status, page=page, page_size=page_size, since=since
    )
    summary = _donchian_summary(since)
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
        "rule_report": _rule_report(marks, since),
    }


def _fresh_perf(equity: float) -> dict:
    from src.donchian import store
    from src.web.book import ANCHOR, book_since, fresh_total

    since = book_since("fresh")
    n = store.count_closed(since) if since else 0
    pnl = store.sum_closed_pnl(since) if since else 0.0
    total = fresh_total(equity)
    pct = (total / ANCHOR - 1.0) * 100.0

    def row(metric: str, live: str) -> dict:
        return {
            "metric": metric,
            "live_txt": live,
            "limit_txt": "—",
            "bt_txt": "tab Lịch sử",
            "verdict": "—",
        }

    return {
        "bt_label": "tab Lịch sử",
        "live_label": "Từ mốc 1051",
        "limit_label": "—",
        "live_days": 0,
        "limit_days": 0,
        "rows": [
            row("Lệnh đã đóng", str(n)),
            row("Realized", f"{pnl:+.2f}"),
            row("Total vs vốn gốc", f"{total:.0f} / {ANCHOR:.0f} ({pct:+.1f}%)"),
        ],
    }


def _perf_compare_payload(book: str = "history") -> dict:
    try:
        if book == "fresh":
            account = get_account_balance()
            return _fresh_perf(float(account.equity or 0))
        from src.web.perf_compare import build_live_vs_bt

        return build_live_vs_bt()
    except Exception:  # noqa: BLE001
        return {
            "bt_label": "BT Config A",
            "live_label": "Live",
            "limit_label": "Từ limit",
            "live_days": 0,
            "limit_days": 0,
            "rows": [],
        }


def _dashboard_context() -> dict:
    from src.web.book import book_since

    account = get_account_balance()
    since = book_since("fresh")
    open_payload = _donchian_trades_payload(status="open", page=1, page_size=50, since=since)
    closed_payload = _donchian_trades_payload(
        status="closed", page=1, page_size=50, since=since
    )
    from src.donchian import store

    daily = store.daily_stats(30, since=since)
    try:
        from src.spot_transfer import dashboard_payload as spot_dashboard

        spot = _rebase_spot_book(
            spot_dashboard(page=1, page_size=20, since_today=True),
            float(account.equity or 0),
            "fresh",
        )
    except Exception:  # noqa: BLE001
        spot = {
            "status": {},
            "rows": [],
            "risk": {},
            "page": 1,
            "pages": 1,
            "total": 0,
            "page_size": 20,
            "since": None,
            "total_skimmed": 0.0,
            "skim_count": 0,
            "capital_safety": {},
            "preview": {},
            "months": [],
        }
    return {
        "exchange_name": EXCHANGE_DISPLAY_NAME,
        "bot_title": "Donchian Trend",
        "account": account,
        "last_cycle_at": get_last_cycle_at(),
        "trading_enabled": is_trading_enabled(),
        "spot_transfer": spot,
        "perf_compare": _perf_compare_payload("fresh"),
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
            "rule_report": open_payload.get("rule_report") or {},
        },
    }


def _live_account() -> tuple[float, float]:
    """(equity, available) from the WS account cache, else the last cycle snapshot. No REST."""
    try:
        from src.exchange.binance_ws import get_balance_from_ws

        bal = get_balance_from_ws()
        if bal is not None and float(bal.account_equity or 0) > 0:
            return float(bal.account_equity), float(bal.available or 0)
    except Exception:  # noqa: BLE001
        pass
    account = get_account_balance()
    if float(account.equity or 0) > 0:
        return float(account.equity), float(account.available or 0)
    snaps = db.get_equity_snapshots(datetime.now(timezone.utc) - timedelta(days=2))
    if snaps:
        last = snaps[-1]
        return float(last["equity"] or 0), float(last["available"] or 0)
    return 0.0, 0.0


def _parse_utc(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _vn_from_ms(ms: int | None) -> str:
    if not ms:
        return ""
    return format_vn_time(datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat())


def _exchange_positions() -> list | None:
    try:
        from src.exchange.binance_ws import get_all_positions_from_ws

        return get_all_positions_from_ws()
    except Exception:  # noqa: BLE001
        return None


def _group_positions(open_lots: list[dict], exchange: list | None, marks: dict[str, float]) -> list[dict]:
    """One row per symbol+side like Binance's Positions tab; compare size with the WS position cache."""
    groups: dict[tuple[str, str], dict] = {}
    for lot in open_lots:
        key = (str(lot["symbol"]), str(lot["side"]))
        g = groups.setdefault(
            key,
            {"symbol": key[0], "side": key[1], "lots": 0, "size": 0.0, "cost": 0.0,
             "margin_usdt": 0.0, "unrealized_pnl": 0.0, "has_mark": True, "tp_band": None},
        )
        size = float(lot["size"] or 0)
        g["lots"] += 1
        g["size"] += size
        g["cost"] += float(lot["entry"] or 0) * size
        g["margin_usdt"] += float(lot["margin_usdt"] or 0)
        if lot.get("unrealized_pnl") is None:
            g["has_mark"] = False
        else:
            g["unrealized_pnl"] += float(lot["unrealized_pnl"])
        if lot.get("tp_band"):
            g["tp_band"] = float(lot["tp_band"])

    ex_map: dict[tuple[str, str], object] = {}
    for pos in exchange or []:
        if float(pos.size or 0) > 0 and pos.side in ("long", "short"):
            ex_map[(str(pos.symbol).upper(), str(pos.side))] = pos
            if (str(pos.symbol).upper(), str(pos.side)) not in groups:
                groups[(str(pos.symbol).upper(), str(pos.side))] = {
                    "symbol": str(pos.symbol).upper(), "side": str(pos.side), "lots": 0, "size": 0.0,
                    "cost": 0.0, "margin_usdt": 0.0, "unrealized_pnl": 0.0, "has_mark": False, "tp_band": None,
                }

    rows = []
    for key in sorted(groups):
        g = groups[key]
        size = g["size"]
        entry = g["cost"] / size if size > 0 else 0.0
        mark = float(marks.get(g["symbol"], 0.0) or 0.0)
        unreal = g["unrealized_pnl"] if g["has_mark"] and g["lots"] else None
        pos = ex_map.get(key)
        ex_size = float(pos.size) if pos is not None else 0.0
        ex_entry = float(pos.avg_price or 0) if pos is not None else 0.0
        if exchange is None:
            sync = "unknown"
        elif abs(ex_size - size) <= max(size, ex_size) * 1e-6:
            sync = "ok"
        else:
            sync = "mismatch"
        ex_unreal = None
        if pos is not None and mark > 0 and ex_entry > 0:
            sign = 1.0 if g["side"] == "long" else -1.0
            ex_unreal = sign * (mark - ex_entry) * ex_size
        # Binance shows position margin and ROI on mark notional / leverage, not on entry margin.
        mark_margin = size * mark / LEVERAGE if mark > 0 and LEVERAGE > 0 else 0.0
        rows.append(
            {
                "symbol": g["symbol"],
                "side": g["side"],
                "lots": g["lots"],
                "size": size,
                "entry": entry,
                "mark": mark,
                "tp_band": g["tp_band"],
                "margin_usdt": g["margin_usdt"],
                "mark_margin_usdt": mark_margin,
                "unrealized_pnl": unreal,
                "roi_pct": (unreal / mark_margin * 100) if unreal is not None and mark_margin > 0 else None,
                "roi_entry_pct": (unreal / g["margin_usdt"] * 100) if unreal is not None and g["margin_usdt"] > 0 else None,
                "exchange_size": ex_size if exchange is not None else None,
                "exchange_entry": ex_entry if pos is not None else None,
                "exchange_unrealized": ex_unreal,
                "sync": sync,
            }
        )
    return rows


def _dual_payload() -> dict:
    from src.donchian import store
    from src.donchian.config import DUAL_LIMIT_ENABLED, ENTRY_TTL_BARS, MAX_COINS
    from src.donchian.dual_limit import ENTRY_DUAL_LIMIT
    from src.web.book import dual_book, dual_withdrawn

    equity, available = _live_account()
    book = dual_book()
    since = book["at"] if book else None
    anchor = float(book["anchor"]) if book else equity

    open_rows = store.get_open_lots()
    pending_rows = store.get_pending_entries()
    symbols = sorted({str(r["symbol"]) for r in open_rows} | {str(r["symbol"]) for r in pending_rows})
    marks = _fetch_symbol_marks(symbols)
    now_ts = datetime.now(timezone.utc).timestamp()

    open_lots = []
    unrealized = 0.0
    legacy_unrealized = 0.0
    dual_open_n = 0
    for row in open_rows:
        item = _serialize_lot(row, marks, now_ts=now_ts)
        item["legacy"] = bool(since and str(row["opened_at"] or "") < since)
        is_dual = item["entry_rule"] == ENTRY_DUAL_LIMIT
        dual_open_n += is_dual
        if item["unrealized_pnl"] is not None:
            if is_dual:
                unrealized += float(item["unrealized_pnl"])
            else:
                legacy_unrealized += float(item["unrealized_pnl"])
        open_lots.append(item)

    closed_pnls: list[float] = []
    for row in store.list_all_lots():
        if str(row["status"]) != "closed" or row["pnl_usdt"] is None:
            continue
        if str(row["entry_rule"] or "") != ENTRY_DUAL_LIMIT:
            continue
        closed_pnls.append(float(row["pnl_usdt"]))
    closed_rows, closed_total = store.list_lots_paged(
        status="closed", page=1, page_size=50, entry_rule=ENTRY_DUAL_LIMIT
    )
    closed = [_serialize_lot(row, marks, now_ts=now_ts) for row in closed_rows]

    pending = []
    for row in pending_rows:
        sym = str(row["symbol"])
        mark = marks.get(sym, 0.0)
        price = float(row["price"] or 0)
        pending.append(
            {
                "id": int(row["id"]),
                "symbol": sym,
                "side": str(row["side"]),
                "price": price,
                "size": float(row["size"] or 0),
                "margin_usdt": float(row["margin_usdt"] or 0),
                "tp_band": float(row["tp_band"] or 0),
                "mark": mark,
                "gap_pct": ((mark - price) / price * 100) if mark > 0 and price > 0 else None,
                "placed_at": format_vn_time(str(row["created_at"] or "")),
                "expires_at": _vn_from_ms(row["expire_ms"]),
            }
        )

    withdrawn = dual_withdrawn(book)
    total = equity + withdrawn
    points = []
    since_dt = _parse_utc(since) if since else None
    if since_dt is not None:
        snaps = db.get_equity_snapshots(since_dt)
        stride = max(1, len(snaps) // 400)
        for i, snap in enumerate(snaps):
            if i % stride and i != len(snaps) - 1:
                continue
            points.append({"t": format_vn_time(str(snap["recorded_at"])), "equity": float(snap["equity"])})

    daily = store.daily_stats(30, since=since, entry_rule=ENTRY_DUAL_LIMIT)
    if since_dt is not None:
        from zoneinfo import ZoneInfo

        start_day = since_dt.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
        daily = [d for d in daily if d["day"] >= start_day]

    wins = [p for p in closed_pnls if p > 0]
    losses = [p for p in closed_pnls if p <= 0]
    gross_loss = abs(sum(losses))
    coins = sorted(store.active_coins())
    return {
        "enabled": DUAL_LIMIT_ENABLED,
        "anchor": {
            "equity": anchor,
            "at": format_vn_time(since) if since else "",
            "set": book is not None,
        },
        "account": {
            "equity": equity,
            "available": available,
            "withdrawn": withdrawn,
            "total": total,
            "vs_anchor": (total - anchor) if equity > 0 else None,
            "vs_anchor_pct": ((total / anchor - 1.0) * 100) if equity > 0 and anchor > 0 else None,
        },
        "pnl": {
            "realized": sum(closed_pnls),
            "unrealized": unrealized,
            "dual_open_n": dual_open_n,
            "legacy_unrealized": legacy_unrealized,
            "legacy_open_n": len(open_rows) - dual_open_n,
            "closed_n": len(closed_pnls),
            "win_rate": (len(wins) / len(closed_pnls) * 100) if closed_pnls else None,
            "pf": (sum(wins) / gross_loss) if gross_loss > 0 else None,
        },
        "coins": {"used": len(coins), "max": MAX_COINS, "list": coins},
        "entry_stats": store.entry_order_stats(since),
        "ttl_bars": ENTRY_TTL_BARS,
        "open_lots": open_lots,
        "positions": _group_positions(open_lots, _exchange_positions(), marks),
        "pending": pending,
        "closed": closed,
        "closed_total": closed_total,
        "daily": daily,
        "equity_points": points,
        "trading_enabled": is_trading_enabled(),
        "last_cycle_at": format_vn_time(get_last_cycle_at()),
    }


def _smc_mark(symbol: str) -> float:
    try:
        from src.exchange.binance_ws import get_mark_from_ws

        mark = get_mark_from_ws(symbol)
        if mark is not None and mark > 0:
            return float(mark)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _smc_payload() -> dict:
    from src.donchian.config import FIXED_SCAN_SYMBOLS
    from src.smc import store as smc_store

    smc_store.ensure_schema()
    closed = [smc_store.row_dict(row) for row in smc_store.closed_rows()]
    active = [smc_store.row_dict(row) for row in smc_store.active_rows()]
    wins = [float(row["pnl_usdt"] or 0) for row in closed if float(row["pnl_usdt"] or 0) > 0]
    losses = [float(row["pnl_usdt"] or 0) for row in closed if float(row["pnl_usdt"] or 0) <= 0]
    realized = sum(float(row["pnl_usdt"] or 0) for row in closed)
    gross_loss = abs(sum(losses))
    unrealized = 0.0
    open_view = []
    pending_view = []
    for row in active:
        mark = _smc_mark(str(row["symbol"]))
        if row["status"] == "open":
            entry = float(row["fill_px"] or row["entry_px"] or 0)
            qty = float(row["qty"] or 0)
            side = str(row["side"])
            upnl = 0.0
            if mark > 0 and qty > 0:
                upnl = (mark - entry) * qty if side == "long" else (entry - mark) * qty
            unrealized += upnl
            open_view.append({**row, "mark": mark, "upnl": upnl})
        else:
            pending_view.append(row)
    by_symbol: dict[str, dict] = {}
    for symbol in FIXED_SCAN_SYMBOLS:
        by_symbol[symbol] = {
            "symbol": symbol,
            "closed_n": 0,
            "wins": 0,
            "pnl": 0.0,
            "open_n": 0,
            "pending_n": 0,
            "upnl": 0.0,
        }
    for row in closed:
        bucket = by_symbol.setdefault(
            str(row["symbol"]),
            {"symbol": row["symbol"], "closed_n": 0, "wins": 0, "pnl": 0.0, "open_n": 0, "pending_n": 0, "upnl": 0.0},
        )
        bucket["closed_n"] += 1
        pnl = float(row["pnl_usdt"] or 0)
        bucket["pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
    for row in open_view:
        bucket = by_symbol.setdefault(
            str(row["symbol"]),
            {"symbol": row["symbol"], "closed_n": 0, "wins": 0, "pnl": 0.0, "open_n": 0, "pending_n": 0, "upnl": 0.0},
        )
        bucket["open_n"] += 1
        bucket["upnl"] += float(row["upnl"] or 0)
    for row in pending_view:
        bucket = by_symbol.setdefault(
            str(row["symbol"]),
            {"symbol": row["symbol"], "closed_n": 0, "wins": 0, "pnl": 0.0, "open_n": 0, "pending_n": 0, "upnl": 0.0},
        )
        bucket["pending_n"] += 1
    coins = []
    for bucket in by_symbol.values():
        n = bucket["closed_n"]
        bucket["win_rate"] = (bucket["wins"] / n * 100) if n else None
        coins.append(bucket)
    coins.sort(key=lambda item: item["pnl"], reverse=True)
    account = get_account_balance()
    recent = [smc_store.row_dict(row) for row in smc_store.list_recent(40)]
    return {
        "account": {
            "equity": account.equity,
            "available": account.available,
            "margin_coin": account.margin_coin,
        },
        "summary": {
            "realized": realized,
            "unrealized": unrealized,
            "total": realized + unrealized,
            "closed_n": len(closed),
            "wins": len(wins),
            "win_rate": (len(wins) / len(closed) * 100) if closed else None,
            "pf": (sum(wins) / gross_loss) if gross_loss > 0 else None,
            "open_n": len(open_view),
            "pending_n": len(pending_view),
            "coins": len(FIXED_SCAN_SYMBOLS),
        },
        "coins": coins,
        "open": open_view,
        "pending": pending_view,
        "recent": recent,
        "trading_enabled": is_trading_enabled(),
        "last_cycle_at": format_vn_time(get_last_cycle_at()),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "smc.html",
        {
            "exchange_name": EXCHANGE_DISPLAY_NAME,
            "bot_title": "SMC quét thanh khoản",
        },
    )


@app.get("/api/smc/status")
def api_smc_status() -> dict:
    payload = _smc_payload()
    try:
        from src.exchange import binance as binance_mod

        payload["rate_limited"] = binance_mod.is_rate_limited()
        payload["rate_limit_remaining_sec"] = binance_mod.rate_limit_remaining_sec()
    except Exception:  # noqa: BLE001
        payload["rate_limited"] = False
        payload["rate_limit_remaining_sec"] = 0.0
    return payload


@app.get("/donchian", response_class=HTMLResponse)
def donchian_dashboard(request: Request) -> HTMLResponse:
    """Previous dual-limit dashboard, kept for reference."""
    return templates.TemplateResponse(
        request,
        "dual.html",
        {
            "exchange_name": EXCHANGE_DISPLAY_NAME,
            "bot_title": "Donchian 2 chiều",
            "payload": _dual_payload(),
        },
    )


@app.get("/api/dual/status")
def api_dual_status() -> dict:
    """New dashboard data. DB + WS cache only, never REST."""
    payload = _dual_payload()
    try:
        from src.exchange import binance as binance_mod

        payload["rate_limited"] = binance_mod.is_rate_limited()
        payload["rate_limit_remaining_sec"] = binance_mod.rate_limit_remaining_sec()
    except Exception:  # noqa: BLE001
        payload["rate_limited"] = False
        payload["rate_limit_remaining_sec"] = 0.0
    return payload


@app.get("/legacy", response_class=HTMLResponse)
def legacy_dashboard(request: Request) -> HTMLResponse:
    """Previous dashboard, hidden from the main page for now."""
    return templates.TemplateResponse(request, "index.html", _dashboard_context())


@app.get("/api/status")
def api_status(book: str = Query(default="fresh")) -> dict:
    kind = "history" if book.strip().lower() == "history" else "fresh"
    from src.web.book import book_since

    account = get_account_balance()
    donchian = _donchian_summary(book_since(kind))
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
        "spot_transfer": _spot_api_payload(book=kind),
        "perf_compare": _perf_compare_payload(kind),
        "rate_limited": rate_limited,
        "rate_limit_remaining_sec": rate_limit_remaining_sec,
    }


def _rebase_spot_book(payload: dict, equity: float, book: str) -> dict:
    payload["book"] = book
    if book != "fresh":
        cs = payload.get("capital_safety") or {}
        cs["book"] = "history"
        payload["capital_safety"] = cs
        return payload
    from src.web.book import ANCHOR, epoch_vn_date, fresh_total

    cut = epoch_vn_date()
    months = list(payload.get("months") or [])
    if cut:
        months = [row for row in months if str(row.get("last_date") or "") >= cut]
    payload["months"] = months
    withdrawn = round(sum(float(row.get("amount") or 0) for row in months), 2)
    payload["withdrawn_gross"] = withdrawn
    total = fresh_total(equity)
    cs = dict(payload.get("capital_safety") or {})
    cs.update(
        {
            "principal": ANCHOR,
            "futures_equity": equity,
            "total_skimmed": withdrawn,
            "manual_excluded": 0.0,
            "total_wealth": total,
            "vs_principal": total - ANCHOR,
            "above_principal": total >= ANCHOR,
            "book": "fresh",
        }
    )
    payload["capital_safety"] = cs
    return payload


def _spot_api_payload(*, page: int = 1, page_size: int = 20, book: str = "fresh") -> dict:
    kind = "history" if book == "history" else "fresh"
    try:
        from src.spot_transfer import dashboard_payload

        payload = dashboard_payload(page=page, page_size=page_size, since_today=True)
    except Exception:  # noqa: BLE001
        payload = {
            "status": {},
            "rows": [],
            "risk": {},
            "page": 1,
            "pages": 1,
            "total": 0,
            "page_size": page_size,
            "since": None,
            "total_skimmed": 0.0,
            "skim_count": 0,
            "capital_safety": {},
            "preview": {},
            "months": [],
        }
    return _rebase_spot_book(payload, float(get_account_balance().equity or 0), kind)


@app.get("/api/spot-transfers")
def api_spot_transfers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=5, le=100),
    book: str = Query(default="fresh"),
) -> dict:
    """Lịch sử rút futures→spot từ hôm nay (+07), có phân trang."""
    return _spot_api_payload(page=page, page_size=page_size, book=book)


@app.get("/api/donchian/trades")
def api_donchian_trades(
    status: str = Query(default="open"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=20, le=100),
    book: str = Query(default="fresh"),
) -> dict:
    from src.web.book import book_since

    kind = status.strip().lower()
    if kind not in {"open", "closed"}:
        kind = "open"
    view = "history" if book.strip().lower() == "history" else "fresh"
    return _donchian_trades_payload(
        status=kind, page=page, page_size=page_size, since=book_since(view)
    )


@app.get("/api/rsi-rev/trades")
def api_rsi_rev_trades(
    status: str = Query(default="open"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=20, le=100),
    book: str = Query(default="fresh"),
) -> dict:
    """Alias kept for dashboard JS compatibility."""
    from src.web.book import book_since

    kind = status.strip().lower()
    if kind not in {"open", "closed"}:
        kind = "open"
    view = "history" if book.strip().lower() == "history" else "fresh"
    return _donchian_trades_payload(
        status=kind, page=page, page_size=page_size, since=book_since(view)
    )


@app.get("/api/donchian/daily")
def api_donchian_daily(
    days: int = Query(default=30, ge=1, le=90),
    book: str = Query(default="fresh"),
) -> dict:
    from src.donchian import store
    from src.web.book import book_since

    view = "history" if book.strip().lower() == "history" else "fresh"
    return {"days": days, "rows": store.daily_stats(days, since=book_since(view))}


@app.get("/api/rsi-rev/daily")
def api_rsi_rev_daily(
    days: int = Query(default=30, ge=1, le=90),
    book: str = Query(default="fresh"),
) -> dict:
    """Alias kept for dashboard JS compatibility."""
    from src.donchian import store
    from src.web.book import book_since

    view = "history" if book.strip().lower() == "history" else "fresh"
    return {"days": days, "rows": store.daily_stats(days, since=book_since(view))}


@app.get("/api/equity-history")
def api_equity_history(range: str = Query(default="7d")) -> dict:
    return build_equity_history_payload(range)


@app.get("/api/wealth-daily")
def api_wealth_daily(
    range: str = Query(default="30d"),
    book: str = Query(default="fresh"),
) -> dict:
    """Daily EOD futures + total. Fresh book is rebased to the 1051 anchor."""
    view = "history" if book.strip().lower() == "history" else "fresh"
    return build_wealth_daily_payload(range, book=view)


@app.post("/settings/trading/start")
def form_start_trading() -> RedirectResponse:
    set_trading_enabled(True)
    return RedirectResponse(url="/", status_code=303)


@app.post("/settings/trading/stop")
def form_stop_trading() -> RedirectResponse:
    set_trading_enabled(False)
    return RedirectResponse(url="/", status_code=303)

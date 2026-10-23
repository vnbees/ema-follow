"""Record each filled trade's minute path. Does not place or cancel orders.

A trade closed by the 41-minute rule keeps being marked until price reaches
its original stop or target. Samples come from the mark already on the
websocket. Nothing here changes order size, stops, or the hold limit.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from src.database import get_connection
from src.smc.broker import MAKER_FEE_RATE, TAKER_FEE_RATE

MINUTE_MS = 60_000
LOOKBACK_MS = 15 * MINUTE_MS
FLAT_BPS = 1.0
_MARK_KEEP_MS = 20 * MINUTE_MS
_MATCH_SLACK_MS = 90_000
_LIVE: dict[int, dict] = {}
_MARKS: dict[str, list[tuple[int, float]]] = {}


def path_pnl(side: str, fill: float, qty: float, fee_open: float, price: float, *, maker: bool) -> float:
    fee_close = qty * price * (MAKER_FEE_RATE if maker else TAKER_FEE_RATE)
    gross = (price - fill) * qty if side == "long" else (fill - price) * qty
    return gross - fee_open - fee_close


def bracket_outcomes(trade) -> tuple[float | None, float | None]:
    """Whole-trade USDT if the remaining size hits the take-profit or the stop.

    The take-profit uses the maker fee. The stop uses the taker fee. A size cut
    already booked on the row is included, because that cash is part of the trade.
    """
    fill = float(trade["fill_px"] or trade["entry_px"] or 0)
    qty = float(trade["qty"] or 0)
    target = float(trade["tp_px"] or 0)
    stop = float(trade["stop_px"] or 0)
    if fill <= 0 or qty <= 0 or target <= 0 or stop <= 0:
        return None, None
    fee_open = float(trade["fee_open"] or 0)
    cut = float(trade["cut_pnl"] or 0) if "cut_pnl" in trade.keys() else 0.0
    side = str(trade["side"])
    tp = path_pnl(side, fill, qty, fee_open, target, maker=True) + cut
    sl = path_pnl(side, fill, qty, fee_open, stop, maker=False) + cut
    return tp, sl


def level_reference(trades: list[dict]) -> dict:
    """Dashboard reference from the shown trades. Does not size or close anything."""
    tp_vals = [float(row["tp_if_usdt"]) for row in trades if row.get("tp_if_usdt") is not None]
    sl_vals = [float(row["sl_if_usdt"]) for row in trades if row.get("sl_if_usdt") is not None]
    closed = [
        row for row in trades
        if row.get("status") == "closed" and row.get("pnl_usdt") is not None
    ]
    wins = [row for row in closed if float(row["pnl_usdt"]) > 0]
    win_rate = (len(wins) / len(closed)) if closed else None
    avg_tp = sum(tp_vals) / len(tp_vals) if tp_vals else None
    avg_sl = sum(sl_vals) / len(sl_vals) if sl_vals else None
    acceptable = None
    if win_rate is not None and 0 < win_rate < 1 and avg_tp is not None and avg_tp > 0:
        acceptable = -(avg_tp * win_rate / (1.0 - win_rate))
    elif win_rate == 0:
        acceptable = 0.0
    return {
        "avg_tp": avg_tp,
        "avg_sl": avg_sl,
        "win_rate": None if win_rate is None else win_rate * 100,
        "acceptable_sl": acceptable,
        "n": len(tp_vals),
        "closed_n": len(closed),
    }


def exit_level(side: str, stop: float, target: float, high: float, low: float) -> tuple[float, str] | None:
    """Stop wins when the same minute reaches both prices."""
    if side == "long":
        if low <= stop:
            return stop, "sl"
        if high >= target:
            return target, "tp"
    elif high >= stop:
        return stop, "sl"
    elif low <= target:
        return target, "tp"
    return None


def _epoch_ms(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def ensure_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS smc_track_point (
            trade_id INTEGER NOT NULL,
            minute_ms INTEGER NOT NULL,
            price REAL NOT NULL,
            pnl_usdt REAL NOT NULL,
            PRIMARY KEY (trade_id, minute_ms)
        );
        CREATE TABLE IF NOT EXISTS smc_track_shadow (
            trade_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            hit_at TEXT,
            hit_px REAL,
            pnl_usdt REAL
        );
        CREATE TABLE IF NOT EXISTS smc_track_bias (
            trade_id INTEGER PRIMARY KEY,
            direction TEXT NOT NULL,
            past_px REAL NOT NULL,
            move_bps REAL NOT NULL
        );
        """
    )


def prior_bias(side: str, fill: float, past: float) -> tuple[str, float] | None:
    """Whether `fill` moved with the trade versus the price 15 minutes earlier.

    Positive bps means the move into the fill was in the trade's direction.
    A move smaller than one basis point is flat.
    """
    if fill <= 0 or past <= 0:
        return None
    change = (fill - past) / past
    signed = change if side == "long" else -change
    bps = signed * 10_000
    if abs(bps) < FLAT_BPS:
        direction = "flat"
    elif bps > 0:
        direction = "with"
    else:
        direction = "against"
    return direction, bps


def remember_mark(symbol: str, now_ms: int, price: float, book: dict[str, list[tuple[int, float]]]) -> None:
    """Keep one mark per minute so a later fill can look 15 minutes back."""
    if price <= 0:
        return
    minute = int(now_ms) - (int(now_ms) % MINUTE_MS)
    rows = book.setdefault(str(symbol), [])
    if rows and rows[-1][0] == minute:
        rows[-1] = (minute, float(price))
    else:
        rows.append((minute, float(price)))
    cutoff = int(now_ms) - _MARK_KEEP_MS
    while len(rows) > 1 and rows[0][0] < cutoff:
        rows.pop(0)


def past_price(symbol: str, opened_ms: int, book: dict[str, list[tuple[int, float]]]) -> float | None:
    """Mark nearest to 15 minutes before the fill, when that minute was recorded."""
    target = int(opened_ms) - LOOKBACK_MS
    chosen: tuple[int, float] | None = None
    for ts, price in book.get(str(symbol), []):
        if ts <= target:
            chosen = (ts, price)
        else:
            break
    if chosen is None or target - chosen[0] > _MATCH_SLACK_MS:
        return None
    return chosen[1]


def prior_direction(
    symbol: str,
    side: str,
    fill: float,
    opened_at: str,
    book: dict[str, list[tuple[int, float]]],
    now_ms: int,
) -> str | None:
    """`with`, `against`, or `flat` from the mark 15 minutes before the fill.

    None means the mark buffer is still too short to decide. `unknown` means
    that minute was never recorded, so the caller must not guess.
    """
    del now_ms
    opened = _epoch_ms(opened_at)
    if opened is None or fill <= 0:
        return "unknown"
    judged = bias_for_trade(
        {"id": 0, "symbol": symbol, "side": side, "fill_px": fill, "opened_at": opened_at},
        book,
    )
    if judged is not None:
        return str(judged["direction"])
    rows = book.get(str(symbol)) or []
    if not rows:
        return None
    if int(rows[0][0]) > opened - LOOKBACK_MS + _MATCH_SLACK_MS:
        return "unknown"
    return None


def bias_for_trade(trade: dict, book: dict[str, list[tuple[int, float]]]) -> dict | None:
    opened = _epoch_ms(trade.get("opened_at"))
    fill = float(trade.get("fill_px") or 0)
    if opened is None or fill <= 0:
        return None
    past = past_price(str(trade.get("symbol") or ""), opened, book)
    if past is None:
        return None
    judged = prior_bias(str(trade.get("side") or ""), fill, past)
    if judged is None:
        return None
    direction, bps = judged
    return {
        "trade_id": int(trade["id"]),
        "direction": direction,
        "past_px": past,
        "move_bps": bps,
    }


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def observe(now_ms: int, marks: dict[str, float], trades: list[dict], live: dict[int, dict]) -> list[dict]:
    """Turn the current marks into path rows. `live` keeps the open minute."""
    events: list[dict] = []
    seen: set[int] = set()
    for trade in trades:
        trade_id = int(trade["id"])
        if not trade.get("opened_at") or float(trade.get("qty") or 0) <= 0:
            continue
        seen.add(trade_id)
        status = str(trade.get("status") or "")
        reason = str(trade.get("close_reason") or "")
        shadow = str(trade.get("shadow_status") or "")
        if status == "closed" and reason in {"tp", "stop"} and shadow not in {"tp", "sl"}:
            events.append(
                {
                    "kind": "shadow",
                    "trade_id": trade_id,
                    "status": "tp" if reason == "tp" else "sl",
                    "hit_at": str(trade.get("closed_at") or ""),
                    "hit_px": float(trade.get("close_px") or 0),
                    "pnl": float(trade.get("pnl_usdt") or 0),
                }
            )
            live.pop(trade_id, None)
            continue
        if shadow in {"tp", "sl"}:
            live.pop(trade_id, None)
            continue
        mark = float(marks.get(str(trade["symbol"])) or 0)
        if mark <= 0:
            continue
        minute = now_ms - (now_ms % MINUTE_MS)
        bar = live.get(trade_id)
        if bar is None or int(bar["minute_ms"]) != minute:
            bar = {"minute_ms": minute, "high": mark, "low": mark, "last": mark}
            live[trade_id] = bar
        else:
            bar["high"] = max(float(bar["high"]), mark)
            bar["low"] = min(float(bar["low"]), mark)
            bar["last"] = mark
        hit = None
        if status == "closed" and reason not in {"tp", "stop"}:
            hit = exit_level(
                str(trade["side"]),
                float(trade["stop_px"]),
                float(trade["tp_px"]),
                float(bar["high"]),
                float(bar["low"]),
            )
        events.extend(_finish_minute(trade, bar, hit=hit, now_ms=now_ms))
        if hit is not None:
            live.pop(trade_id, None)
    for trade_id in [key for key in live if key not in seen]:
        live.pop(trade_id, None)
    return events


def _finish_minute(trade: dict, bar: dict, *, hit: tuple[float, str] | None = None, now_ms: int | None = None) -> list[dict]:
    side = str(trade["side"])
    fill = float(trade["fill_px"] or trade["entry_px"] or 0)
    qty = float(trade["qty"] or 0)
    fee_open = float(trade["fee_open"] or 0)
    if hit is None:
        price = float(bar["last"])
        pnl = path_pnl(side, fill, qty, fee_open, price, maker=False)
        return [
            {
                "kind": "point",
                "trade_id": int(trade["id"]),
                "minute_ms": int(bar["minute_ms"]),
                "price": price,
                "pnl": pnl,
            }
        ]
    price, kind = hit
    pnl = path_pnl(side, fill, qty, fee_open, price, maker=(kind == "tp"))
    when = now_ms if now_ms is not None else int(bar["minute_ms"]) + MINUTE_MS
    return [
        {
            "kind": "point",
            "trade_id": int(trade["id"]),
            "minute_ms": int(bar["minute_ms"]),
            "price": price,
            "pnl": pnl,
        },
        {
            "kind": "shadow",
            "trade_id": int(trade["id"]),
            "status": kind,
            "hit_at": _iso(when),
            "hit_px": price,
            "pnl": pnl,
        },
    ]


def _watch_rows(conn) -> list[dict]:
    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT t.*, s.status AS shadow_status
        FROM smc_trades t
        LEFT JOIN smc_track_shadow s ON s.trade_id = t.id
        WHERE t.opened_at IS NOT NULL
          AND IFNULL(t.qty, 0) > 0
          AND (
            t.status = 'open'
            OR (
              t.status = 'closed'
              AND IFNULL(t.close_reason, '') NOT IN ('tp', 'stop', 'busy', 'cooldown', 'through', 'small')
              AND IFNULL(s.status, '') NOT IN ('tp', 'sl')
            )
            OR (
              t.status = 'closed'
              AND t.close_reason IN ('tp', 'stop')
              AND s.trade_id IS NULL
            )
          )
        """
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _apply(conn, events: list[dict]) -> None:
    ensure_schema(conn)
    for event in events:
        if event["kind"] == "point":
            conn.execute(
                """
                INSERT INTO smc_track_point (trade_id, minute_ms, price, pnl_usdt)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_id, minute_ms) DO UPDATE SET
                    price = excluded.price,
                    pnl_usdt = excluded.pnl_usdt
                """,
                (event["trade_id"], event["minute_ms"], event["price"], event["pnl"]),
            )
        elif event["kind"] == "bias":
            conn.execute(
                """
                INSERT INTO smc_track_bias (trade_id, direction, past_px, move_bps)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_id) DO NOTHING
                """,
                (event["trade_id"], event["direction"], event["past_px"], event["move_bps"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO smc_track_shadow (trade_id, status, hit_at, hit_px, pnl_usdt)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    status = excluded.status,
                    hit_at = excluded.hit_at,
                    hit_px = excluded.hit_px,
                    pnl_usdt = excluded.pnl_usdt
                """,
                (event["trade_id"], event["status"], event["hit_at"], event["hit_px"], event["pnl"]),
            )


def _remember_scan_marks(now_ms: int, book: dict[str, list[tuple[int, float]]]) -> None:
    """Read marks already on the websocket. Does not request candles or orders."""
    try:
        from src.donchian.config import FIXED_SCAN_SYMBOLS
    except Exception as exc:  # noqa: BLE001
        logging.debug("SMC track symbols skipped: %s", exc)
        return
    for symbol in FIXED_SCAN_SYMBOLS:
        remember_mark(str(symbol), now_ms, _mark(str(symbol)), book)


def _bias_candidates(conn, now_ms: int) -> list[dict]:
    ensure_schema(conn)
    earliest = _iso(now_ms - _MARK_KEEP_MS)
    rows = conn.execute(
        """
        SELECT t.id, t.symbol, t.side, t.fill_px, t.opened_at
        FROM smc_trades t
        LEFT JOIN smc_track_bias b ON b.trade_id = t.id
        WHERE t.opened_at IS NOT NULL
          AND t.opened_at >= ?
          AND IFNULL(t.qty, 0) > 0
          AND IFNULL(t.fill_px, 0) > 0
          AND IFNULL(t.close_reason, '') NOT IN ('busy', 'cooldown', 'through', 'small')
          AND b.trade_id IS NULL
        """,
        (earliest,),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def sample(now_ms: int | None = None) -> None:
    """One watcher tick. Reads marks, then writes the path. No orders."""
    from src.smc import store

    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    _remember_scan_marks(now, _MARKS)
    with store._lock, get_connection() as conn:
        trades = _watch_rows(conn)
        pending_bias = _bias_candidates(conn, now)
    marks: dict[str, float] = {}
    for trade in trades:
        symbol = str(trade["symbol"])
        if symbol in marks:
            continue
        marks[symbol] = _mark(symbol)
    events = observe(now, marks, trades, _LIVE)
    for trade in pending_bias:
        judged = bias_for_trade(trade, _MARKS)
        if judged is not None:
            events.append({"kind": "bias", **judged})
    if not events:
        return
    with store._lock, get_connection() as conn:
        _apply(conn, events)


def _mark(symbol: str) -> float:
    try:
        from src.exchange.binance_ws import get_mark_from_ws

        mark = get_mark_from_ws(symbol)
        if mark is not None and mark > 0:
            return float(mark)
    except Exception:  # noqa: BLE001
        logging.debug("SMC track mark %s unavailable", symbol)
    return 0.0


def _hold_sec(start: str | None, end: str | None, now_ms: int) -> int | None:
    opened = _epoch_ms(start)
    if opened is None:
        return None
    closed = _epoch_ms(end)
    end_ms = closed if closed is not None else now_ms
    return max(0, (end_ms - opened) // 1000)


def track_rows(now_ms: int | None = None) -> list[dict]:
    from src.smc import store

    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    with store._lock, get_connection() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT t.*, s.status AS shadow_status, s.hit_at AS shadow_hit_at,
                   s.hit_px AS shadow_hit_px, s.pnl_usdt AS shadow_pnl,
                   b.direction AS prior_dir, b.move_bps AS prior_bps
            FROM smc_trades t
            LEFT JOIN smc_track_shadow s ON s.trade_id = t.id
            LEFT JOIN smc_track_bias b ON b.trade_id = t.id
            WHERE t.opened_at IS NOT NULL
              AND IFNULL(t.qty, 0) > 0
              AND IFNULL(t.close_reason, '') NOT IN ('busy', 'cooldown', 'through', 'small')
            ORDER BY t.id DESC
            """
        ).fetchall()
    out = []
    for row in rows:
        reason = str(row["close_reason"] or "")
        shadow = str(row["shadow_status"] or "")
        if reason == "tp" and not shadow:
            shadow = "tp"
        elif reason == "stop" and not shadow:
            shadow = "sl"
        elif row["status"] == "open" and not shadow:
            shadow = "watching"
        elif row["status"] == "closed" and reason not in {"tp", "stop"} and not shadow:
            shadow = "watching"
        hit_at = row["shadow_hit_at"]
        shadow_pnl = row["shadow_pnl"]
        if reason in {"tp", "stop"} and shadow_pnl is None:
            hit_at = row["closed_at"]
            shadow_pnl = row["pnl_usdt"]
        out.append(
            {
                "id": int(row["id"]),
                "symbol": row["symbol"],
                "side": row["side"],
                "status": row["status"],
                "close_reason": reason,
                "opened_at": row["opened_at"],
                "closed_at": row["closed_at"],
                "hold_sec": _hold_sec(row["opened_at"], row["closed_at"] if row["status"] == "closed" else None, now),
                "pnl_usdt": row["pnl_usdt"],
                "fill_px": row["fill_px"],
                "close_px": row["close_px"],
                "stop_px": row["stop_px"],
                "tp_px": row["tp_px"],
                "shadow_status": shadow,
                "shadow_hit_at": hit_at,
                "shadow_pnl": shadow_pnl,
                "shadow_hold_sec": _hold_sec(row["opened_at"], hit_at, now) if hit_at else (
                    _hold_sec(row["opened_at"], None, now) if shadow == "watching" else None
                ),
                "prior_dir": row["prior_dir"] if "prior_dir" in row.keys() else None,
                "prior_bps": row["prior_bps"] if "prior_bps" in row.keys() else None,
                "hedge_status": row["hedge_status"] if "hedge_status" in row.keys() else None,
                "hedge_side": row["hedge_side"] if "hedge_side" in row.keys() else None,
                "hedge_pnl": row["hedge_pnl"] if "hedge_pnl" in row.keys() else None,
                "size_cut": row["size_cut"] if "size_cut" in row.keys() else None,
            }
        )
        tp_if, sl_if = bracket_outcomes(row)
        out[-1]["tp_if_usdt"] = tp_if
        out[-1]["sl_if_usdt"] = sl_if
    return out


def track_path(trade_id: int) -> dict | None:
    from src.smc import store

    with store._lock, get_connection() as conn:
        ensure_schema(conn)
        trade = conn.execute("SELECT * FROM smc_trades WHERE id = ?", (int(trade_id),)).fetchone()
        if trade is None or not trade["opened_at"]:
            return None
        points = conn.execute(
            "SELECT minute_ms, price, pnl_usdt FROM smc_track_point WHERE trade_id = ? ORDER BY minute_ms",
            (int(trade_id),),
        ).fetchall()
        shadow = conn.execute("SELECT * FROM smc_track_shadow WHERE trade_id = ?", (int(trade_id),)).fetchone()
        bias = conn.execute("SELECT direction, move_bps FROM smc_track_bias WHERE trade_id = ?", (int(trade_id),)).fetchone()
    opened_ms = _epoch_ms(trade["opened_at"]) or 0
    opened_minute = opened_ms - (opened_ms % MINUTE_MS)
    return {
        "id": int(trade["id"]),
        "symbol": trade["symbol"],
        "side": trade["side"],
        "close_reason": trade["close_reason"],
        "opened_at": trade["opened_at"],
        "closed_at": trade["closed_at"],
        "pnl_usdt": trade["pnl_usdt"],
        "points": [
            {
                "minute_ms": int(point["minute_ms"]),
                "minute": max(0, (int(point["minute_ms"]) - opened_minute) // MINUTE_MS),
                "price": point["price"],
                "pnl": point["pnl_usdt"],
            }
            for point in points
        ],
        "shadow_status": None if shadow is None else shadow["status"],
        "shadow_hit_at": None if shadow is None else shadow["hit_at"],
        "shadow_pnl": None if shadow is None else shadow["pnl_usdt"],
        "prior_dir": None if bias is None else bias["direction"],
        "prior_bps": None if bias is None else bias["move_bps"],
    }


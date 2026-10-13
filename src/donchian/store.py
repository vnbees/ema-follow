from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.database import get_connection

_lock = threading.Lock()
_VN = ZoneInfo("Asia/Ho_Chi_Minh")

REASON_TP = "TP_BAND"
REASON_EOD = "EOD_OPEN"

REASON_LABELS = {
    REASON_TP: "Chạm Donchian band",
    REASON_EOD: "Đóng thủ công / EOD",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    ddl = """
        CREATE TABLE IF NOT EXISTS donchian_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            trend TEXT NOT NULL,
            trend_ts INTEGER,
            status TEXT NOT NULL DEFAULT 'open',
            entry_ts INTEGER,
            entry_px REAL NOT NULL,
            tp_band REAL NOT NULL,
            size REAL NOT NULL,
            margin_usdt REAL,
            notional_usdt REAL,
            entry_order_id TEXT,
            close_order_id TEXT,
            close_ts INTEGER,
            close_px REAL,
            close_reason TEXT,
            pnl_usdt REAL,
            fee_open_usdt REAL,
            fee_close_usdt REAL,
            body_atr REAL,
            pot_rr REAL,
            size_mult REAL,
            opp_band REAL,
            entry_rule TEXT NOT NULL DEFAULT 'standard',
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_donchian_lots_open
            ON donchian_lots(status, symbol);
        CREATE INDEX IF NOT EXISTS idx_donchian_lots_opened
            ON donchian_lots(opened_at);
        CREATE INDEX IF NOT EXISTS idx_donchian_lots_closed
            ON donchian_lots(closed_at);

        CREATE TABLE IF NOT EXISTS donchian_state (
            symbol TEXT PRIMARY KEY,
            trend TEXT,
            trend_ts INTEGER,
            waiting_entry INTEGER NOT NULL DEFAULT 0,
            prev_parallel INTEGER NOT NULL DEFAULT 0,
            last_processed_ts INTEGER,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS donchian_skips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS donchian_entry_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair_key TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            order_id TEXT NOT NULL,
            client_oid TEXT,
            price REAL NOT NULL,
            size REAL NOT NULL,
            margin_usdt REAL NOT NULL,
            size_mult REAL,
            body_atr REAL,
            pot_rr REAL,
            tp_band REAL NOT NULL,
            opp_band REAL,
            trend TEXT,
            trend_ts INTEGER,
            entry_ts INTEGER,
            entry_atr REAL,
            why TEXT,
            expire_ms INTEGER NOT NULL,
            filled_qty REAL,
            avg_px REAL,
            lot_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_donchian_entry_orders_status
            ON donchian_entry_orders(status, symbol);
        CREATE INDEX IF NOT EXISTS idx_donchian_entry_orders_oid
            ON donchian_entry_orders(order_id);
    """
    if conn is not None:
        conn.executescript(ddl)
        _migrate_donchian_state(conn)
        _migrate_donchian_lots(conn)
        return
    with get_connection() as owned:
        owned.executescript(ddl)
        _migrate_donchian_state(owned)
        _migrate_donchian_lots(owned)


def _migrate_donchian_state(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(donchian_state)")}
    if "last_processed_ts" not in cols:
        conn.execute("ALTER TABLE donchian_state ADD COLUMN last_processed_ts INTEGER")


def _migrate_donchian_lots(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(donchian_lots)")}
    if "close_order_id" not in cols:
        conn.execute("ALTER TABLE donchian_lots ADD COLUMN close_order_id TEXT")
    for col, decl in (
        ("body_atr", "REAL"),
        ("pot_rr", "REAL"),
        ("size_mult", "REAL"),
        ("opp_band", "REAL"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE donchian_lots ADD COLUMN {col} {decl}")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(donchian_lots)")}
    for col, decl in (
        ("tp_limit_order_id", "TEXT"),
        ("tp_limit_px", "REAL"),
        ("tp_limit_qty", "REAL"),
        ("entry_rule", "TEXT"),
        ("entry_atr", "REAL"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE donchian_lots ADD COLUMN {col} {decl}")


def save_state(
    symbol: str,
    *,
    trend: str | None,
    trend_ts: int | None,
    waiting_entry: bool,
    prev_parallel: bool,
    last_processed_ts: int | None = None,
) -> None:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO donchian_state (
                symbol, trend, trend_ts, waiting_entry, prev_parallel, last_processed_ts, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                trend=excluded.trend, trend_ts=excluded.trend_ts,
                waiting_entry=excluded.waiting_entry, prev_parallel=excluded.prev_parallel,
                last_processed_ts=excluded.last_processed_ts,
                updated_at=excluded.updated_at
            """,
            (
                symbol.upper(),
                trend,
                trend_ts,
                int(waiting_entry),
                int(prev_parallel),
                last_processed_ts,
                now,
            ),
        )


def load_state(symbol: str) -> dict | None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM donchian_state WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def insert_lot(
    *,
    symbol: str,
    side: str,
    trend: str,
    trend_ts: int | None,
    entry_ts: int | None,
    entry_px: float,
    tp_band: float,
    size: float,
    margin_usdt: float,
    notional_usdt: float,
    entry_order_id: str,
    fee_open_usdt: float = 0.0,
    body_atr: float | None = None,
    pot_rr: float | None = None,
    size_mult: float | None = None,
    opp_band: float | None = None,
    entry_rule: str = "standard",
    entry_atr: float | None = None,
) -> int:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO donchian_lots (
                symbol, side, trend, trend_ts, status, entry_ts, entry_px, tp_band,
                size, margin_usdt, notional_usdt, entry_order_id, fee_open_usdt,
                body_atr, pot_rr, size_mult, opp_band, entry_rule, entry_atr,
                opened_at, updated_at
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                side.lower(),
                trend,
                trend_ts,
                entry_ts,
                entry_px,
                tp_band,
                size,
                margin_usdt,
                notional_usdt,
                entry_order_id,
                float(fee_open_usdt or 0),
                body_atr,
                pot_rr,
                size_mult,
                opp_band,
                entry_rule or "standard",
                entry_atr,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def set_tp_limit(lot_id: int, order_id: str, price: float, qty: float | None = None) -> None:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE donchian_lots
            SET tp_limit_order_id = ?, tp_limit_px = ?, tp_limit_qty = ?, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (str(order_id), float(price), qty, now, int(lot_id)),
        )


def clear_tp_limit(lot_id: int) -> None:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE donchian_lots
            SET tp_limit_order_id = NULL, tp_limit_px = NULL, tp_limit_qty = NULL, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (now, int(lot_id)),
        )


def clear_tp_limit_by_order(order_id: str) -> None:
    oid = str(order_id or "").strip()
    if not oid:
        return
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE donchian_lots
            SET tp_limit_order_id = NULL, tp_limit_px = NULL, tp_limit_qty = NULL, updated_at = ?
            WHERE status = 'open' AND tp_limit_order_id = ?
            """,
            (now, oid),
        )


def find_open_lot_by_tp_order(order_id: str) -> sqlite3.Row | None:
    rows = find_open_lots_by_tp_order(order_id)
    return rows[0] if rows else None


def find_open_lots_by_tp_order(order_id: str) -> list[sqlite3.Row]:
    """Lots on one coin and side share a single resting TP limit."""
    oid = str(order_id or "").strip()
    if not oid:
        return []
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            """
            SELECT * FROM donchian_lots
            WHERE status = 'open' AND tp_limit_order_id = ?
            ORDER BY id
            """,
            (oid,),
        ).fetchall()


def close_lot(
    lot_id: int,
    *,
    close_px: float,
    close_reason: str,
    pnl_usdt: float,
    fee_close_usdt: float = 0.0,
    close_order_id: str = "",
    close_ts: int | None = None,
) -> bool:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE donchian_lots
            SET status = 'closed', close_px = ?, close_reason = ?, pnl_usdt = ?,
                fee_close_usdt = ?, close_ts = ?, close_order_id = ?,
                closed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (
                close_px, close_reason, pnl_usdt, float(fee_close_usdt or 0),
                close_ts, close_order_id or "",
                now, now, lot_id,
            ),
        )
        return cur.rowcount > 0


def get_lot(lot_id: int) -> sqlite3.Row | None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            "SELECT * FROM donchian_lots WHERE id = ?", (lot_id,)
        ).fetchone()


def get_open_lots(symbol: str | None = None) -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        if symbol:
            return conn.execute(
                "SELECT * FROM donchian_lots WHERE status = 'open' AND symbol = ? ORDER BY id",
                (symbol.upper(),),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM donchian_lots WHERE status = 'open' ORDER BY id"
        ).fetchall()


def has_open_lot_for_symbol(symbol: str) -> bool:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT id FROM donchian_lots WHERE status = 'open' AND symbol = ? LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return row is not None


def count_open() -> int:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM donchian_lots WHERE status = 'open'"
        ).fetchone()
        return int(row["n"]) if row else 0


def count_closed(since: str | None = None) -> int:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        if since:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM donchian_lots
                WHERE status = 'closed' AND closed_at >= ?
                """,
                (since,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM donchian_lots WHERE status = 'closed'"
            ).fetchone()
        return int(row["n"]) if row else 0


def sum_closed_pnl(since: str | None = None) -> float:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        if since:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(pnl_usdt), 0) AS s FROM donchian_lots
                WHERE status = 'closed' AND closed_at >= ?
                """,
                (since,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl_usdt), 0) AS s FROM donchian_lots WHERE status = 'closed'"
            ).fetchone()
        return float(row["s"] or 0)


def list_lots_paged(
    *,
    status: str,
    page: int = 1,
    page_size: int = 50,
    since: str | None = None,
    entry_rule: str | None = None,
) -> tuple[list[sqlite3.Row], int]:
    page = max(1, page)
    page_size = min(100, max(20, page_size))
    offset = (page - 1) * page_size
    order_col = "opened_at" if status == "open" else "closed_at"
    since_sql = ""
    params: list = [status]
    # Open lots stay visible. The fresh book only hides closes from before the anchor.
    if since and status == "closed":
        since_sql = " AND closed_at >= ?"
        params.append(since)
    if entry_rule:
        since_sql += " AND entry_rule = ?"
        params.append(entry_rule)
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM donchian_lots WHERE status = ?{since_sql}",
            params,
        ).fetchone()
        total = int(total_row["n"]) if total_row else 0
        rows = conn.execute(
            f"""
            SELECT * FROM donchian_lots
            WHERE status = ?{since_sql}
            ORDER BY {order_col} DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, offset),
        ).fetchall()
        return rows, total


def list_all_lots() -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            "SELECT * FROM donchian_lots ORDER BY opened_at DESC, id DESC"
        ).fetchall()


def record_skip(symbol: str, reason: str) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO donchian_skips (symbol, reason, created_at) VALUES (?, ?, ?)",
            (symbol.upper(), reason, _utc_now()),
        )


def _vn_day_expr(col: str) -> str:
    return f"date(replace(replace({col}, 'T', ' '), 'Z', ''), '+7 hours')"


def daily_stats(days: int = 30, since: str | None = None, entry_rule: str | None = None) -> list[dict[str, Any]]:
    days = max(1, min(90, days))
    open_expr = _vn_day_expr("opened_at")
    close_expr = _vn_day_expr("closed_at")
    open_since = " AND opened_at >= ?" if since else ""
    close_since = " AND closed_at >= ?" if since else ""
    open_params: tuple = (since,) if since else ()
    close_params: tuple = (since,) if since else ()
    if entry_rule:
        open_since += " AND entry_rule = ?"
        close_since += " AND entry_rule = ?"
        open_params += (entry_rule,)
        close_params += (entry_rule,)
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        opened = {
            str(r["d"]): int(r["n"])
            for r in conn.execute(
                f"""
                SELECT {open_expr} AS d, COUNT(*) AS n
                FROM donchian_lots
                WHERE opened_at IS NOT NULL{open_since}
                GROUP BY d
                """,
                open_params,
            )
        }
        closed_rows = conn.execute(
            f"""
            SELECT {close_expr} AS d, close_reason, COUNT(*) AS n,
                   COALESCE(SUM(pnl_usdt), 0) AS pnl
            FROM donchian_lots
            WHERE status = 'closed' AND closed_at IS NOT NULL{close_since}
            GROUP BY d, close_reason
            """,
            close_params,
        ).fetchall()

    from datetime import timedelta
    today = datetime.now(_VN).date()
    by_day: dict[str, dict[str, Any]] = {}
    for day, n in opened.items():
        by_day.setdefault(day, _empty_day(day))["opened"] = n
    for row in closed_rows:
        day = str(row["d"])
        rec = by_day.setdefault(day, _empty_day(day))
        rec["closed"] += int(row["n"])
        rec["pnl"] += float(row["pnl"] or 0)
        if str(row["close_reason"] or "") == REASON_TP:
            rec["tp"] += int(row["n"])

    out: list[dict[str, Any]] = []
    for offset in range(days):
        d = (today - timedelta(days=offset)).isoformat()
        out.append(by_day.get(d, _empty_day(d)))
    return out


def _empty_day(day: str) -> dict[str, Any]:
    return {"day": day, "opened": 0, "closed": 0, "tp": 0, "pnl": 0.0}


ENTRY_PENDING = "pending"
ENTRY_FILLED = "filled"
ENTRY_CANCELED = "canceled"


def insert_entry_order(
    *,
    pair_key: str,
    symbol: str,
    side: str,
    order_id: str,
    client_oid: str,
    price: float,
    size: float,
    margin_usdt: float,
    tp_band: float,
    expire_ms: int,
    size_mult: float | None = None,
    body_atr: float | None = None,
    pot_rr: float | None = None,
    opp_band: float | None = None,
    trend: str | None = None,
    trend_ts: int | None = None,
    entry_ts: int | None = None,
    entry_atr: float | None = None,
    why: str = "",
) -> int:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO donchian_entry_orders (
                pair_key, symbol, side, status, order_id, client_oid, price, size,
                margin_usdt, size_mult, body_atr, pot_rr, tp_band, opp_band, trend,
                trend_ts, entry_ts, entry_atr, why, expire_ms, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pair_key,
                symbol.upper(),
                side.lower(),
                str(order_id),
                client_oid or "",
                float(price),
                float(size),
                float(margin_usdt),
                size_mult,
                body_atr,
                pot_rr,
                float(tp_band),
                opp_band,
                trend,
                trend_ts,
                entry_ts,
                entry_atr,
                why or "",
                int(expire_ms),
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def get_pending_entries(symbol: str | None = None) -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        if symbol:
            return conn.execute(
                "SELECT * FROM donchian_entry_orders WHERE status = 'pending' AND symbol = ? ORDER BY id",
                (symbol.upper(),),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM donchian_entry_orders WHERE status = 'pending' ORDER BY id"
        ).fetchall()


def find_pending_entry_by_order(order_id: str) -> sqlite3.Row | None:
    oid = str(order_id or "").strip()
    if not oid:
        return None
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            "SELECT * FROM donchian_entry_orders WHERE status = 'pending' AND order_id = ? LIMIT 1",
            (oid,),
        ).fetchone()


def pending_entry_order_ids() -> set[str]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT order_id FROM donchian_entry_orders WHERE status = 'pending'"
        ).fetchall()
        return {str(row["order_id"]) for row in rows}


def claim_entry(entry_id: int, *, status: str, filled_qty: float, avg_px: float | None) -> bool:
    """Move a pending entry to its final status once. False if another thread won."""
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE donchian_entry_orders
            SET status = ?, filled_qty = ?, avg_px = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status, float(filled_qty or 0), avg_px, now, int(entry_id)),
        )
        return cur.rowcount > 0


def set_entry_lot(entry_id: int, lot_id: int) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE donchian_entry_orders SET lot_id = ?, updated_at = ? WHERE id = ?",
            (int(lot_id), _utc_now(), int(entry_id)),
        )


def list_entry_orders(*, since: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    since_sql = " WHERE created_at >= ?" if since else ""
    params: tuple = (since,) if since else ()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            f"SELECT * FROM donchian_entry_orders{since_sql} ORDER BY id DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()


def entry_order_stats(since: str | None = None) -> dict[str, int]:
    since_sql = " WHERE created_at >= ?" if since else ""
    params: tuple = (since,) if since else ()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT status, COUNT(*) AS n,
                   SUM(CASE WHEN COALESCE(filled_qty, 0) > 0 THEN 1 ELSE 0 END) AS with_fill
            FROM donchian_entry_orders{since_sql}
            GROUP BY status
            """,
            params,
        ).fetchall()
    out = {"placed": 0, "pending": 0, "filled": 0, "canceled": 0, "partial": 0}
    for row in rows:
        n = int(row["n"] or 0)
        out["placed"] += n
        status = str(row["status"])
        if status == ENTRY_PENDING:
            out["pending"] += n
        elif status == ENTRY_FILLED:
            out["filled"] += n
        else:
            out["canceled"] += n
            out["partial"] += int(row["with_fill"] or 0)
    return out


def active_coins() -> set[str]:
    """Coins that hold an open lot or a pending entry limit."""
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT symbol FROM donchian_lots WHERE status = 'open'
            UNION
            SELECT symbol FROM donchian_entry_orders WHERE status = 'pending'
            """
        ).fetchall()
        return {str(row["symbol"]).upper() for row in rows}

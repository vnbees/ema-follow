from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.database import get_connection

_lock = threading.Lock()
_VN = ZoneInfo("Asia/Ho_Chi_Minh")

REASON_TP = "TP"
REASON_BE = "BE_AFTER_7D"
REASON_TIMEOUT = "TIMEOUT_30D"
REASON_EOD = "EOD_OPEN"

REASON_LABELS = {
    REASON_TP: "TP về vùng RSI",
    REASON_BE: "Break-even sau 7 ngày",
    REASON_TIMEOUT: "Timeout 30 ngày",
    REASON_EOD: "Đóng thủ công / EOD",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    ddl = """
            CREATE TABLE IF NOT EXISTS rsi_rev_pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                zone TEXT NOT NULL,
                anchor_ts INTEGER NOT NULL,
                anchor_price REAL NOT NULL,
                anchor_rsi REAL NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(symbol, anchor_ts, zone)
            );
            CREATE INDEX IF NOT EXISTS idx_rsi_rev_pending_symbol
                ON rsi_rev_pending(symbol);

            CREATE TABLE IF NOT EXISTS rsi_rev_lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                zone TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                anchor_ts INTEGER NOT NULL,
                anchor_price REAL NOT NULL,
                anchor_rsi REAL,
                entry REAL NOT NULL,
                tp REAL NOT NULL,
                size REAL NOT NULL,
                margin_usdt REAL,
                notional_usdt REAL,
                entry_order_id TEXT,
                client_oid TEXT,
                signal_ts INTEGER,
                close_price REAL,
                close_reason TEXT,
                pnl_usdt REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rsi_rev_lots_open
                ON rsi_rev_lots(status, symbol);
            CREATE INDEX IF NOT EXISTS idx_rsi_rev_lots_opened
                ON rsi_rev_lots(opened_at);
            CREATE INDEX IF NOT EXISTS idx_rsi_rev_lots_closed
                ON rsi_rev_lots(closed_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_rsi_rev_lots_dedupe
                ON rsi_rev_lots(symbol, anchor_ts, side)
                WHERE status = 'open';

            CREATE TABLE IF NOT EXISTS rsi_rev_skips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
    if conn is not None:
        conn.executescript(ddl)
        return
    with get_connection() as owned:
        owned.executescript(ddl)


def insert_pending(
    *,
    symbol: str,
    zone: str,
    anchor_ts: int,
    anchor_price: float,
    anchor_rsi: float,
) -> bool:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        try:
            conn.execute(
                """
                INSERT INTO rsi_rev_pending (
                    symbol, zone, anchor_ts, anchor_price, anchor_rsi, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol.upper(), zone, anchor_ts, anchor_price, anchor_rsi, now),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def list_pending(symbol: str | None = None) -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        if symbol:
            return conn.execute(
                "SELECT * FROM rsi_rev_pending WHERE symbol = ? ORDER BY anchor_ts",
                (symbol.upper(),),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM rsi_rev_pending ORDER BY anchor_ts"
        ).fetchall()


def count_pending() -> int:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT COUNT(*) AS n FROM rsi_rev_pending").fetchone()
        return int(row["n"]) if row else 0


def delete_pending(pending_id: int) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM rsi_rev_pending WHERE id = ?", (pending_id,))


def insert_lot(
    *,
    symbol: str,
    side: str,
    zone: str,
    anchor_ts: int,
    anchor_price: float,
    anchor_rsi: float,
    entry: float,
    tp: float,
    size: float,
    margin_usdt: float,
    notional_usdt: float,
    entry_order_id: str,
    client_oid: str,
    signal_ts: int,
) -> int:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO rsi_rev_lots (
                symbol, side, zone, status, anchor_ts, anchor_price, anchor_rsi,
                entry, tp, size, margin_usdt, notional_usdt, entry_order_id, client_oid,
                signal_ts, opened_at, updated_at
            ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                side.lower(),
                zone,
                anchor_ts,
                anchor_price,
                anchor_rsi,
                entry,
                tp,
                size,
                margin_usdt,
                notional_usdt,
                entry_order_id,
                client_oid,
                signal_ts,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def close_lot(
    lot_id: int,
    *,
    close_price: float,
    close_reason: str,
    pnl_usdt: float,
) -> bool:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE rsi_rev_lots
            SET status = 'closed', close_price = ?, close_reason = ?, pnl_usdt = ?,
                closed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (close_price, close_reason, pnl_usdt, now, now, lot_id),
        )
        return cur.rowcount > 0


def get_open_lots(symbol: str | None = None) -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        if symbol:
            return conn.execute(
                """
                SELECT * FROM rsi_rev_lots
                WHERE status = 'open' AND symbol = ?
                ORDER BY id
                """,
                (symbol.upper(),),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM rsi_rev_lots WHERE status = 'open' ORDER BY id"
        ).fetchall()


def get_lot(lot_id: int) -> sqlite3.Row | None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            "SELECT * FROM rsi_rev_lots WHERE id = ?", (lot_id,)
        ).fetchone()


def has_open_lot(symbol: str, anchor_ts: int, side: str) -> bool:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            """
            SELECT id FROM rsi_rev_lots
            WHERE status = 'open' AND symbol = ? AND anchor_ts = ? AND side = ?
            LIMIT 1
            """,
            (symbol.upper(), anchor_ts, side.lower()),
        ).fetchone()
        return row is not None


def count_open() -> int:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM rsi_rev_lots WHERE status = 'open'"
        ).fetchone()
        return int(row["n"]) if row else 0


def count_closed() -> int:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM rsi_rev_lots WHERE status = 'closed'"
        ).fetchone()
        return int(row["n"]) if row else 0


def sum_closed_pnl() -> float:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_usdt), 0) AS s FROM rsi_rev_lots WHERE status = 'closed'"
        ).fetchone()
        return float(row["s"] or 0)


def list_lots_paged(
    *,
    status: str,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[sqlite3.Row], int]:
    page = max(1, page)
    page_size = min(100, max(20, page_size))
    offset = (page - 1) * page_size
    order_col = "opened_at" if status == "open" else "closed_at"
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        total_row = conn.execute(
            "SELECT COUNT(*) AS n FROM rsi_rev_lots WHERE status = ?",
            (status,),
        ).fetchone()
        total = int(total_row["n"]) if total_row else 0
        rows = conn.execute(
            f"""
            SELECT * FROM rsi_rev_lots
            WHERE status = ?
            ORDER BY {order_col} DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (status, page_size, offset),
        ).fetchall()
        return rows, total


def record_skip(symbol: str, reason: str) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO rsi_rev_skips (symbol, reason, created_at) VALUES (?, ?, ?)",
            (symbol.upper(), reason, _utc_now()),
        )


def _vn_day_expr(col: str) -> str:
    return f"date(replace(replace({col}, 'T', ' '), 'Z', ''), '+7 hours')"


def daily_stats(days: int = 30) -> list[dict[str, Any]]:
    days = max(1, min(90, days))
    open_expr = _vn_day_expr("opened_at")
    close_expr = _vn_day_expr("closed_at")
    skip_expr = _vn_day_expr("created_at")
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        opened = {
            str(r["d"]): int(r["n"])
            for r in conn.execute(
                f"""
                SELECT {open_expr} AS d, COUNT(*) AS n
                FROM rsi_rev_lots
                WHERE opened_at IS NOT NULL
                GROUP BY d
                """
            )
        }
        closed_rows = conn.execute(
            f"""
            SELECT {close_expr} AS d, close_reason, COUNT(*) AS n,
                   COALESCE(SUM(pnl_usdt), 0) AS pnl
            FROM rsi_rev_lots
            WHERE status = 'closed' AND closed_at IS NOT NULL
            GROUP BY d, close_reason
            """
        ).fetchall()
        skips = {
            str(r["d"]): int(r["n"])
            for r in conn.execute(
                f"""
                SELECT {skip_expr} AS d, COUNT(*) AS n
                FROM rsi_rev_skips
                GROUP BY d
                """
            )
        }

    by_day: dict[str, dict[str, Any]] = {}
    for day, n in opened.items():
        by_day.setdefault(day, _empty_day(day))["opened"] = n
    for row in closed_rows:
        day = str(row["d"])
        rec = by_day.setdefault(day, _empty_day(day))
        reason = str(row["close_reason"] or "")
        rec["closed"] += int(row["n"])
        rec["pnl"] += float(row["pnl"] or 0)
        if reason == REASON_TP:
            rec["tp"] += int(row["n"])
        elif reason == REASON_BE:
            rec["be"] += int(row["n"])
        elif reason == REASON_TIMEOUT:
            rec["timeout"] += int(row["n"])
    for day, n in skips.items():
        by_day.setdefault(day, _empty_day(day))["skips"] = n

    from datetime import timedelta

    today = datetime.now(_VN).date()
    out: list[dict[str, Any]] = []
    for offset in range(days):
        d = (today - timedelta(days=offset)).isoformat()
        out.append(by_day.get(d, _empty_day(d)))
    return out


def _empty_day(day: str) -> dict[str, Any]:
    return {
        "day": day,
        "opened": 0,
        "closed": 0,
        "tp": 0,
        "be": 0,
        "timeout": 0,
        "skips": 0,
        "pnl": 0.0,
    }

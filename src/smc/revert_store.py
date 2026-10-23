"""Book for the return-to-signal limits. Many levels per coin, many orders per level."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

from src.database import get_connection

_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    sql = """
        CREATE TABLE IF NOT EXISTS revert_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            level_px REAL NOT NULL,
            signal_ts INTEGER NOT NULL,
            deadline_ts INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS revert_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            bar_ts INTEGER NOT NULL,
            status TEXT NOT NULL,
            entry_px REAL NOT NULL,
            tp_px REAL NOT NULL,
            qty REAL,
            margin_usdt REAL NOT NULL,
            entry_order_id TEXT,
            tp_order_id TEXT,
            fill_px REAL,
            close_px REAL,
            fee_open REAL NOT NULL DEFAULT 0,
            fee_close REAL NOT NULL DEFAULT 0,
            pnl_usdt REAL,
            close_reason TEXT,
            created_at TEXT NOT NULL,
            opened_at TEXT,
            closed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_revert_signal_symbol ON revert_signals(symbol, status);
        CREATE INDEX IF NOT EXISTS idx_revert_order_signal ON revert_orders(signal_id, status);
        CREATE TABLE IF NOT EXISTS revert_bar_skips (
            signal_id INTEGER NOT NULL,
            bar_ts INTEGER NOT NULL,
            PRIMARY KEY (signal_id, bar_ts)
        );
    """
    if conn is not None:
        conn.executescript(sql)
        return
    with _lock, get_connection() as owned:
        owned.executescript(sql)


def _rows(conn: sqlite3.Connection, query: str, args: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, args).fetchall()]


def get_or_create_signal(
    symbol: str,
    level_px: float,
    signal_ts: int,
    deadline_ts: int,
) -> dict:
    """One active level per formatted price. A repeat of the same price keeps the first clock."""
    symbol = symbol.upper()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        found = conn.execute(
            """
            SELECT * FROM revert_signals
            WHERE symbol = ? AND level_px = ? AND status = 'active'
            ORDER BY id LIMIT 1
            """,
            (symbol, float(level_px)),
        ).fetchone()
        if found is not None:
            row = dict(found)
            if int(signal_ts) < int(row["signal_ts"]):
                conn.execute(
                    "UPDATE revert_signals SET signal_ts = ?, deadline_ts = ? WHERE id = ?",
                    (int(signal_ts), int(deadline_ts), int(row["id"])),
                )
                row["signal_ts"] = int(signal_ts)
                row["deadline_ts"] = int(deadline_ts)
            return row
        now = _utc_now()
        cur = conn.execute(
            """
            INSERT INTO revert_signals (symbol, level_px, signal_ts, deadline_ts, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (symbol, float(level_px), int(signal_ts), int(deadline_ts), now),
        )
        row = conn.execute("SELECT * FROM revert_signals WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def active_signals(symbol: str | None = None) -> list[dict]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        if symbol is None:
            return _rows(conn, "SELECT * FROM revert_signals WHERE status = 'active' ORDER BY symbol, level_px")
        return _rows(
            conn,
            "SELECT * FROM revert_signals WHERE symbol = ? AND status = 'active' ORDER BY level_px",
            (symbol.upper(),),
        )


def mark_signal_expired(signal_id: int) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute("UPDATE revert_signals SET status = 'expired' WHERE id = ? AND status = 'active'", (int(signal_id),))


def side_used(symbol: str, side: str, bar_ts: int) -> bool:
    """This candle already opened the one order allowed for this side."""
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            """
            SELECT 1 FROM revert_orders
            WHERE symbol = ? AND side = ? AND bar_ts = ?
            LIMIT 1
            """,
            (symbol.upper(), side, int(bar_ts)),
        ).fetchone()
        return row is not None


def bar_used(signal_id: int, bar_ts: int) -> bool:
    """One candle opens at most one order on a signal, filled or not."""
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            """
            SELECT 1 FROM revert_orders
            WHERE signal_id = ? AND bar_ts = ?
            LIMIT 1
            """,
            (int(signal_id), int(bar_ts)),
        ).fetchone()
        if row is not None:
            return True
        skipped = conn.execute(
            "SELECT 1 FROM revert_bar_skips WHERE signal_id = ? AND bar_ts = ?",
            (int(signal_id), int(bar_ts)),
        ).fetchone()
        return skipped is not None


def skip_bar(signal_id: int, bar_ts: int) -> None:
    """This candle already opened the one order for this side, on another level."""
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO revert_bar_skips (signal_id, bar_ts) VALUES (?, ?)",
            (int(signal_id), int(bar_ts)),
        )


def insert_order(
    *,
    signal_id: int,
    symbol: str,
    side: str,
    bar_ts: int,
    entry_px: float,
    tp_px: float,
    qty: float,
    margin_usdt: float,
    entry_order_id: str,
) -> dict:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO revert_orders (
                signal_id, symbol, side, bar_ts, status, entry_px, tp_px, qty, margin_usdt,
                entry_order_id, created_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                int(signal_id),
                symbol.upper(),
                side,
                int(bar_ts),
                float(entry_px),
                float(tp_px),
                float(qty),
                float(margin_usdt),
                str(entry_order_id),
                now,
            ),
        )
        row = conn.execute("SELECT * FROM revert_orders WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def orders_for(signal_id: int) -> list[dict]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return _rows(conn, "SELECT * FROM revert_orders WHERE signal_id = ? ORDER BY bar_ts, id", (int(signal_id),))


def open_orders(symbol: str) -> list[dict]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return _rows(
            conn,
            "SELECT * FROM revert_orders WHERE symbol = ? AND status = 'open' ORDER BY id",
            (symbol.upper(),),
        )


def working_orders(symbol: str | None = None) -> list[dict]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        if symbol is None:
            return _rows(conn, "SELECT * FROM revert_orders WHERE status IN ('pending', 'open') ORDER BY id")
        return _rows(
            conn,
            "SELECT * FROM revert_orders WHERE symbol = ? AND status IN ('pending', 'open') ORDER BY id",
            (symbol.upper(),),
        )


def mark_open(order_id: int, *, fill_px: float, fee_open: float) -> bool:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE revert_orders
            SET status = 'open', fill_px = ?, fee_open = ?, opened_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (float(fill_px), float(fee_open), now, int(order_id)),
        )
        return cur.rowcount > 0


def set_tp_order(order_id: int, tp_order_id: str) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE revert_orders SET tp_order_id = ? WHERE id = ?",
            (str(tp_order_id), int(order_id)),
        )


def set_tp_price(order_id: int, tp_px: float) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE revert_orders SET tp_px = ? WHERE id = ? AND status IN ('pending', 'open')",
            (float(tp_px), int(order_id)),
        )


def clear_tp(order_id: int) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute("UPDATE revert_orders SET tp_order_id = NULL WHERE id = ? AND status = 'open'", (int(order_id),))


def mark_closed(
    order_id: int,
    *,
    close_px: float,
    fee_close: float,
    pnl: float,
    reason: str,
) -> bool:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE revert_orders
            SET status = 'closed', close_px = ?, fee_close = ?, pnl_usdt = ?, close_reason = ?, closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (float(close_px), float(fee_close), float(pnl), reason, now, int(order_id)),
        )
        return cur.rowcount > 0


def mark_cancelled(order_id: int, reason: str) -> bool:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE revert_orders
            SET status = 'cancelled', close_reason = ?, closed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (reason, now, int(order_id)),
        )
        return cur.rowcount > 0


def recent_signals(symbol: str) -> list[dict]:
    """Active levels, plus levels that expired recently enough to still explain a closed order."""
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return _rows(
            conn,
            """
            SELECT * FROM revert_signals
            WHERE symbol = ?
            ORDER BY status = 'active' DESC, signal_ts DESC
            LIMIT 40
            """,
            (symbol.upper(),),
        )

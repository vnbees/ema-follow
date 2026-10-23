"""SQLite book for 5m-fade limits and fills. One row per 5m signal."""

from __future__ import annotations

from src.database import get_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fade5m_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    limit_px REAL NOT NULL,
    bar_ts INTEGER NOT NULL,
    bucket_ts INTEGER NOT NULL,
    status TEXT NOT NULL,
    entry_px REAL,
    fee_open REAL NOT NULL DEFAULT 0,
    opened_ts INTEGER,
    entry_order_id TEXT,
    exit_px REAL,
    fee_close REAL,
    pnl REAL,
    reason TEXT,
    closed_ts INTEGER,
    close_order_id TEXT,
    tp_px REAL,
    tp_order_id TEXT,
    atr REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fade5m_symbol_bar ON fade5m_orders(symbol, bar_ts);
CREATE INDEX IF NOT EXISTS idx_fade5m_status ON fade5m_orders(status);
"""


def ensure_schema() -> None:
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(fade5m_orders)")}
        for col, decl in (("tp_px", "REAL"), ("tp_order_id", "TEXT"), ("atr", "REAL")):
            if col not in cols:
                conn.execute(f"ALTER TABLE fade5m_orders ADD COLUMN {col} {decl}")


def has_bar(symbol: str, bar_ts: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM fade5m_orders WHERE symbol = ? AND bar_ts = ?",
            (symbol.upper(), int(bar_ts)),
        ).fetchone()
    return row is not None


def insert_pending(
    *,
    symbol: str,
    side: str,
    qty: float,
    limit_px: float,
    bar_ts: int,
    bucket_ts: int,
    entry_order_id: str,
) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO fade5m_orders (
                symbol, side, qty, limit_px, bar_ts, bucket_ts, status, entry_order_id
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                symbol.upper(),
                side,
                float(qty),
                float(limit_px),
                int(bar_ts),
                int(bucket_ts),
                entry_order_id,
            ),
        )
        row = conn.execute("SELECT * FROM fade5m_orders WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def pending_rows(symbol: str | None = None) -> list[dict]:
    with get_connection() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM fade5m_orders WHERE status = 'pending' AND symbol = ? ORDER BY id",
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM fade5m_orders WHERE status = 'pending' ORDER BY id"
            ).fetchall()
    return [dict(r) for r in rows]


def open_rows(symbol: str | None = None) -> list[dict]:
    with get_connection() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM fade5m_orders WHERE status = 'open' AND symbol = ? ORDER BY id",
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM fade5m_orders WHERE status = 'open' ORDER BY id"
            ).fetchall()
    return [dict(r) for r in rows]


def recent_closed(limit: int = 400) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM fade5m_orders
            WHERE status IN ('closed', 'cancelled')
            ORDER BY COALESCE(closed_ts, 0) DESC, id DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_open(row_id: int, *, entry_px: float, qty: float, fee_open: float, opened_ts: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE fade5m_orders
            SET status = 'open', entry_px = ?, qty = ?, fee_open = ?, opened_ts = ?
            WHERE id = ? AND status = 'pending'
            """,
            (float(entry_px), float(qty), float(fee_open), int(opened_ts), int(row_id)),
        )


def mark_cancelled(row_id: int, reason: str, closed_ts: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE fade5m_orders
            SET status = 'cancelled', reason = ?, closed_ts = ?
            WHERE id = ? AND status = 'pending'
            """,
            (reason, int(closed_ts), int(row_id)),
        )


def set_tp(row_id: int, *, tp_px: float, tp_order_id: str, atr: float) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE fade5m_orders
            SET tp_px = ?, tp_order_id = ?, atr = ?
            WHERE id = ? AND status = 'open'
            """,
            (float(tp_px), str(tp_order_id), float(atr), int(row_id)),
        )


def clear_tp_order(row_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE fade5m_orders SET tp_order_id = NULL WHERE id = ? AND status = 'open'",
            (int(row_id),),
        )


def mark_closed(
    row_id: int,
    *,
    exit_px: float,
    fee_close: float,
    pnl: float,
    reason: str,
    closed_ts: int,
    close_order_id: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE fade5m_orders
            SET status = 'closed', exit_px = ?, fee_close = ?, pnl = ?, reason = ?,
                closed_ts = ?, close_order_id = ?
            WHERE id = ? AND status = 'open'
            """,
            (
                float(exit_px),
                float(fee_close),
                float(pnl),
                reason,
                int(closed_ts),
                close_order_id,
                int(row_id),
            ),
        )

"""SQLite book for 4h Supertrend+ADX positions."""

from __future__ import annotations

from src.database import get_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trend4h_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_px REAL NOT NULL,
    fee_open REAL NOT NULL DEFAULT 0,
    opened_ts INTEGER NOT NULL,
    bar_ts INTEGER NOT NULL,
    status TEXT NOT NULL,
    exit_px REAL,
    fee_close REAL,
    pnl REAL,
    reason TEXT,
    closed_ts INTEGER,
    entry_order_id TEXT,
    close_order_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trend4h_symbol_status ON trend4h_positions(symbol, status);
"""


def ensure_schema() -> None:
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


def open_row(symbol: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM trend4h_positions WHERE symbol = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
    return dict(row) if row else None


def all_open() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trend4h_positions WHERE status = 'open' ORDER BY symbol"
        ).fetchall()
    return [dict(r) for r in rows]


def recent_closed(limit: int = 80) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trend4h_positions WHERE status = 'closed' ORDER BY closed_ts DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_open(
    *,
    symbol: str,
    side: str,
    qty: float,
    entry_px: float,
    fee_open: float,
    opened_ts: int,
    bar_ts: int,
    entry_order_id: str,
) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO trend4h_positions (
                symbol, side, qty, entry_px, fee_open, opened_ts, bar_ts, status, entry_order_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                symbol.upper(),
                side,
                float(qty),
                float(entry_px),
                float(fee_open),
                int(opened_ts),
                int(bar_ts),
                entry_order_id,
            ),
        )
        row = conn.execute("SELECT * FROM trend4h_positions WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def close_open(
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
            UPDATE trend4h_positions
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

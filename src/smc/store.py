"""SQLite book for SMC sweep trades. One pending or open row per symbol."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from src.database import get_connection

_lock = threading.Lock()

ACTIVE = ("pending", "open")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    sql = """
        CREATE TABLE IF NOT EXISTS smc_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            signal_ts INTEGER NOT NULL,
            entry_px REAL NOT NULL,
            stop_px REAL NOT NULL,
            tp_px REAL NOT NULL,
            margin_usdt REAL,
            qty REAL,
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
        CREATE INDEX IF NOT EXISTS idx_smc_status ON smc_trades(status);
        CREATE INDEX IF NOT EXISTS idx_smc_symbol ON smc_trades(symbol);
    """
    if conn is not None:
        conn.executescript(sql)
        _migrate(conn)
        return
    with _lock, get_connection() as owned:
        owned.executescript(sql)
        _migrate(owned)


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(smc_trades)")}
    if "stop_order_id" not in cols:
        conn.execute("ALTER TABLE smc_trades ADD COLUMN stop_order_id TEXT")
    for name, decl in (
        ("hedge_side", "TEXT"),
        ("hedge_qty", "REAL"),
        ("hedge_status", "TEXT"),
        ("hedge_order_id", "TEXT"),
        ("hedge_fill_px", "REAL"),
        ("hedge_close_px", "REAL"),
        ("hedge_pnl", "REAL"),
        ("size_cut", "TEXT"),
        ("cut_pnl", "REAL"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE smc_trades ADD COLUMN {name} {decl}")


def has_signal(symbol: str, signal_ts: int) -> bool:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT 1 FROM smc_trades WHERE symbol = ? AND signal_ts = ? LIMIT 1",
            (symbol.upper(), int(signal_ts)),
        ).fetchone()
        return row is not None


def active_for(symbol: str) -> sqlite3.Row | None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            """
            SELECT * FROM smc_trades
            WHERE symbol = ? AND status IN ('pending', 'open')
            ORDER BY id DESC LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()


def rows_for_symbol(symbol: str) -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return list(
            conn.execute(
                "SELECT * FROM smc_trades WHERE symbol = ? ORDER BY id",
                (symbol.upper(),),
            ).fetchall()
        )


def active_rows() -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return list(
            conn.execute(
                "SELECT * FROM smc_trades WHERE status IN ('pending', 'open') ORDER BY id"
            ).fetchall()
        )


def insert_pending(
    *,
    symbol: str,
    side: str,
    signal_ts: int,
    entry: float,
    stop: float,
    target: float,
    margin: float,
) -> int:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO smc_trades (
                symbol, side, status, signal_ts, entry_px, stop_px, tp_px,
                margin_usdt, created_at
            ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (symbol.upper(), side, int(signal_ts), entry, stop, target, margin, now),
        )
        return int(cur.lastrowid)


def set_entry_order(trade_id: int, order_id: str, qty: float) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE smc_trades SET entry_order_id = ?, qty = ? WHERE id = ? AND status = 'pending'",
            (order_id, qty, trade_id),
        )


def mark_open(
    trade_id: int,
    *,
    fill_px: float,
    qty: float,
    fee_open: float,
) -> bool:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE smc_trades
            SET status = 'open', fill_px = ?, qty = ?, fee_open = ?, opened_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (fill_px, qty, fee_open, now, trade_id),
        )
        return cur.rowcount > 0


def set_tp_order(trade_id: int, order_id: str) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE smc_trades SET tp_order_id = ? WHERE id = ? AND status = 'open'",
            (order_id, trade_id),
        )


def set_stop_order(trade_id: int, order_id: str) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE smc_trades SET stop_order_id = ? WHERE id = ? AND status = 'open'",
            (order_id, trade_id),
        )


def mark_cancelled(trade_id: int, reason: str) -> bool:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE smc_trades
            SET status = 'cancelled', close_reason = ?, closed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (reason, now, trade_id),
        )
        return cur.rowcount > 0


def mark_closed(
    trade_id: int,
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
            UPDATE smc_trades
            SET status = 'closed', close_px = ?, fee_close = ?, pnl_usdt = ?,
                close_reason = ?, closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (close_px, fee_close, pnl, reason, now, trade_id),
        )
        return cur.rowcount > 0


def claim_size_cut(trade_id: int) -> bool:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE smc_trades SET size_cut = 'pending'
            WHERE id = ? AND status = 'open' AND size_cut IS NULL
            """,
            (trade_id,),
        )
        return cur.rowcount > 0


def release_size_cut(trade_id: int) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE smc_trades SET size_cut = NULL
            WHERE id = ? AND size_cut = 'pending'
            """,
            (trade_id,),
        )


def mark_size_full(trade_id: int) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE smc_trades SET size_cut = 'full'
            WHERE id = ? AND size_cut = 'pending'
            """,
            (trade_id,),
        )


def mark_size_half(
    trade_id: int,
    *,
    qty: float,
    fee_open: float,
    margin: float,
    cut_pnl: float,
    label: str = "half",
) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE smc_trades
            SET size_cut = ?, qty = ?, fee_open = ?, margin_usdt = ?, cut_pnl = ?
            WHERE id = ? AND size_cut = 'pending'
            """,
            (label, qty, fee_open, margin, cut_pnl, trade_id),
        )


def claim_hedge(trade_id: int) -> bool:
    """One attempt to open the opposite order. A second caller finds the claim."""
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE smc_trades SET hedge_status = 'pending'
            WHERE id = ? AND status = 'open' AND hedge_status IS NULL
            """,
            (trade_id,),
        )
        return cur.rowcount > 0


def release_hedge_claim(trade_id: int) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE smc_trades SET hedge_status = NULL
            WHERE id = ? AND hedge_status = 'pending'
            """,
            (trade_id,),
        )


def mark_hedge_open(trade_id: int, *, side: str, qty: float, order_id: str, fill_px: float) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE smc_trades
            SET hedge_status = 'open', hedge_side = ?, hedge_qty = ?,
                hedge_order_id = ?, hedge_fill_px = ?
            WHERE id = ? AND hedge_status = 'pending'
            """,
            (side, qty, order_id, fill_px, trade_id),
        )


def mark_hedge_skip(trade_id: int) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE smc_trades SET hedge_status = 'skip'
            WHERE id = ? AND hedge_status IS NULL
            """,
            (trade_id,),
        )
        conn.execute(
            """
            UPDATE smc_trades SET hedge_status = 'skip'
            WHERE id = ? AND hedge_status = 'pending' AND hedge_order_id IS NULL
            """,
            (trade_id,),
        )


def mark_hedge_closed(trade_id: int, *, close_px: float, pnl: float) -> bool:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE smc_trades
            SET hedge_status = 'closed', hedge_close_px = ?, hedge_pnl = ?
            WHERE id = ? AND hedge_status = 'open'
            """,
            (close_px, pnl, trade_id),
        )
        return cur.rowcount > 0


def hedges_still_open() -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return list(
            conn.execute(
                """
                SELECT * FROM smc_trades
                WHERE status = 'closed' AND hedge_status = 'open'
                ORDER BY id
                """
            ).fetchall()
        )


def get_trade(trade_id: int) -> sqlite3.Row | None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute("SELECT * FROM smc_trades WHERE id = ?", (trade_id,)).fetchone()


def find_by_order(order_id: str) -> sqlite3.Row | None:
    oid = str(order_id or "")
    if not oid:
        return None
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            """
            SELECT * FROM smc_trades
            WHERE status IN ('pending', 'open')
              AND (entry_order_id = ? OR tp_order_id = ? OR stop_order_id = ?)
            LIMIT 1
            """,
            (oid, oid, oid),
        ).fetchone()


def list_recent(limit: int = 40) -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return list(
            conn.execute(
                """
                SELECT * FROM smc_trades
                WHERE status IN ('closed', 'cancelled', 'open', 'pending')
                  AND IFNULL(close_reason, '') NOT IN ('busy', 'cooldown', 'through', 'small')
                ORDER BY id DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        )


def closed_rows() -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return list(
            conn.execute(
                "SELECT * FROM smc_trades WHERE status = 'closed' ORDER BY id"
            ).fetchall()
        )


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}

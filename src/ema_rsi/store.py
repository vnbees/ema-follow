from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from src.database import get_connection

_lock = threading.Lock()

REASON_HIT_SL = "HIT_SL"
REASON_HIT_TP = "HIT_TP"
REASON_INVALID_SL = "INVALID_SL"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    ddl = """
            CREATE TABLE IF NOT EXISTS ema_rsi_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                r REAL NOT NULL,
                size REAL NOT NULL,
                margin_usdt REAL,
                entry_order_id TEXT,
                sl_order_id TEXT,
                tp_order_id TEXT,
                client_oid TEXT,
                zone_start_ts INTEGER,
                signal_ts INTEGER,
                close_price REAL,
                close_reason TEXT,
                pnl_usdt REAL,
                open_notified INTEGER NOT NULL DEFAULT 0,
                close_notified INTEGER NOT NULL DEFAULT 0,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ema_rsi_trades_open
                ON ema_rsi_trades(status, symbol);
            CREATE TABLE IF NOT EXISTS ema_rsi_seen_signals (
                symbol TEXT NOT NULL,
                signal_ts INTEGER NOT NULL,
                PRIMARY KEY (symbol, signal_ts)
            );
            """
    if conn is not None:
        conn.executescript(ddl)
        return
    with get_connection() as owned:
        owned.executescript(ddl)


def insert_trade(
    *,
    symbol: str,
    side: str,
    entry: float,
    sl: float,
    tp: float,
    r: float,
    size: float,
    margin_usdt: float,
    entry_order_id: str,
    sl_order_id: str,
    tp_order_id: str,
    client_oid: str,
    zone_start_ts: int,
    signal_ts: int,
    status: str = "open",
    close_reason: str | None = None,
    close_price: float | None = None,
    pnl_usdt: float | None = None,
    open_notified: bool = False,
    close_notified: bool = False,
) -> int:
    now = _utc_now()
    closed_at = now if status != "open" else None
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO ema_rsi_trades (
                symbol, side, status, entry, sl, tp, r, size, margin_usdt,
                entry_order_id, sl_order_id, tp_order_id, client_oid,
                zone_start_ts, signal_ts, close_price, close_reason, pnl_usdt,
                open_notified, close_notified, opened_at, closed_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            """,
            (
                symbol.upper(),
                side.lower(),
                status,
                entry,
                sl,
                tp,
                r,
                size,
                margin_usdt,
                entry_order_id,
                sl_order_id,
                tp_order_id,
                client_oid,
                zone_start_ts,
                signal_ts,
                close_price,
                close_reason,
                pnl_usdt,
                1 if open_notified else 0,
                1 if close_notified else 0,
                now,
                closed_at,
                now,
            ),
        )
        return int(cur.lastrowid)


def update_algo_ids(trade_id: int, *, sl_order_id: str, tp_order_id: str) -> None:
    with _lock, get_connection() as conn:
        conn.execute(
            """
            UPDATE ema_rsi_trades
            SET sl_order_id = ?, tp_order_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (sl_order_id, tp_order_id, _utc_now(), trade_id),
        )


def mark_open_notified(trade_id: int) -> bool:
    with _lock, get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE ema_rsi_trades
            SET open_notified = 1, updated_at = ?
            WHERE id = ? AND open_notified = 0
            """,
            (_utc_now(), trade_id),
        )
        return cur.rowcount > 0


def close_trade(
    trade_id: int,
    *,
    close_price: float,
    close_reason: str,
    pnl_usdt: float,
) -> bool:
    """Return True if this call actually closed an open, un-notified-close row."""
    with _lock, get_connection() as conn:
        row = conn.execute(
            "SELECT status, close_notified FROM ema_rsi_trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        if row is None:
            return False
        if row["status"] != "open" or int(row["close_notified"] or 0):
            return False
        conn.execute(
            """
            UPDATE ema_rsi_trades
            SET status = 'closed',
                close_price = ?,
                close_reason = ?,
                pnl_usdt = ?,
                close_notified = 1,
                closed_at = ?,
                updated_at = ?
            WHERE id = ? AND status = 'open' AND close_notified = 0
            """,
            (close_price, close_reason, pnl_usdt, _utc_now(), _utc_now(), trade_id),
        )
        return True


def get_open_trades() -> list[sqlite3.Row]:
    with get_connection() as conn:
        ensure_schema(conn)
        return list(
            conn.execute(
                "SELECT * FROM ema_rsi_trades WHERE status = 'open' ORDER BY id"
            ).fetchall()
        )


def get_latest_closed_trade(symbol: str, side: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            """
            SELECT * FROM ema_rsi_trades
            WHERE status = 'closed' AND symbol = ? AND side = ?
            ORDER BY id DESC LIMIT 1
            """,
            (symbol.upper(), side.lower()),
        ).fetchone()


def reopen_trade(trade_id: int) -> bool:
    """Undo a false close — position still open on exchange."""
    with _lock, get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE ema_rsi_trades
            SET status = 'open',
                close_price = NULL,
                close_reason = NULL,
                pnl_usdt = NULL,
                close_notified = 0,
                closed_at = NULL,
                updated_at = ?
            WHERE id = ? AND status = 'closed'
            """,
            (_utc_now(), trade_id),
        )
        return cur.rowcount > 0


def get_open_trade_for_symbol(symbol: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            """
            SELECT * FROM ema_rsi_trades
            WHERE status = 'open' AND symbol = ?
            ORDER BY id DESC LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()


def get_trade(trade_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM ema_rsi_trades WHERE id = ?", (trade_id,)
        ).fetchone()


def find_open_by_order_id(order_id: str) -> sqlite3.Row | None:
    if not order_id:
        return None
    with get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            """
            SELECT * FROM ema_rsi_trades
            WHERE status = 'open'
              AND (sl_order_id = ? OR tp_order_id = ? OR entry_order_id = ?)
            ORDER BY id DESC LIMIT 1
            """,
            (order_id, order_id, order_id),
        ).fetchone()


def find_open_by_client_oid(client_oid: str) -> sqlite3.Row | None:
    oid = (client_oid or "").strip()
    if not oid:
        return None
    prefix = oid[:4].lower()
    if prefix in {"ersl", "ertp"}:
        try:
            trade_id = int(oid[4:])
        except ValueError:
            trade_id = 0
        if trade_id:
            row = get_trade(trade_id)
            if row is not None and str(row["status"]) == "open":
                return row
    with get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            """
            SELECT * FROM ema_rsi_trades
            WHERE status = 'open' AND client_oid = ?
            ORDER BY id DESC LIMIT 1
            """,
            (oid,),
        ).fetchone()


def count_open() -> int:
    with get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ema_rsi_trades WHERE status = 'open'"
        ).fetchone()
        return int(row["n"] if row else 0)


def list_trades(limit: int = 100) -> list[sqlite3.Row]:
    with get_connection() as conn:
        ensure_schema(conn)
        return list(
            conn.execute(
                """
                SELECT * FROM ema_rsi_trades
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )


def mark_signal_seen(symbol: str, signal_ts: int) -> bool:
    """Return True if this (symbol, candle) was not seen before."""
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        try:
            conn.execute(
                "INSERT INTO ema_rsi_seen_signals (symbol, signal_ts) VALUES (?, ?)",
                (symbol.upper(), int(signal_ts)),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}

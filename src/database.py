import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from src.config import DATABASE_PATH, DEFAULT_SYMBOL


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_id TEXT,
                client_oid TEXT,
                price REAL NOT NULL,
                size REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                filled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS trade_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                balance_at_open REAL,
                balance_at_close REAL,
                pnl_usdt REAL,
                pnl_pct REAL,
                opened_at TEXT,
                closed_at TEXT,
                status TEXT NOT NULL DEFAULT 'open'
            );
            """
        )
        row = conn.execute("SELECT value FROM settings WHERE key = 'symbol'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('symbol', ?)",
                (DEFAULT_SYMBOL,),
            )


def get_setting(key: str, default: str = "") -> str:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_symbol() -> str:
    return get_setting("symbol", DEFAULT_SYMBOL).upper()


def insert_entry(
    symbol: str,
    side: str,
    order_id: str,
    client_oid: str,
    price: float,
    size: float,
    status: str = "pending",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO entries (symbol, side, order_id, client_oid, price, size, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, side, order_id, client_oid, price, size, status, _utc_now()),
        )
        return int(cursor.lastrowid)


def update_entry_status(entry_id: int, status: str, filled: bool = False) -> None:
    with get_connection() as conn:
        if filled:
            conn.execute(
                "UPDATE entries SET status = ?, filled_at = ? WHERE id = ?",
                (status, _utc_now(), entry_id),
            )
        else:
            conn.execute("UPDATE entries SET status = ? WHERE id = ?", (status, entry_id))


def update_entry_by_order_id(order_id: str, status: str, filled: bool = False) -> None:
    with get_connection() as conn:
        if filled:
            conn.execute(
                "UPDATE entries SET status = ?, filled_at = ? WHERE order_id = ?",
                (status, _utc_now(), order_id),
            )
        else:
            conn.execute("UPDATE entries SET status = ? WHERE order_id = ?", (status, order_id))


def get_pending_entries(symbol: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM entries
            WHERE symbol = ? AND status = 'pending'
            ORDER BY id ASC
            """,
            (symbol,),
        ).fetchall()


def get_filled_entries(symbol: str, side: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM entries
            WHERE symbol = ? AND side = ? AND status = 'filled'
            ORDER BY id ASC
            """,
            (symbol, side),
        ).fetchall()


def compute_avg_entry_price(symbol: str, side: str) -> float | None:
    rows = get_filled_entries(symbol, side)
    if not rows:
        return None
    total_value = sum(float(row["price"]) * float(row["size"]) for row in rows)
    total_size = sum(float(row["size"]) for row in rows)
    if total_size <= 0:
        return None
    return total_value / total_size


def create_trade_cycle(symbol: str, side: str, balance_at_open: float) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trade_cycles (symbol, side, balance_at_open, opened_at, status)
            VALUES (?, ?, ?, ?, 'open')
            """,
            (symbol, side, balance_at_open, _utc_now()),
        )
        return int(cursor.lastrowid)


def close_trade_cycle(cycle_id: int, balance_at_close: float) -> None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT balance_at_open FROM trade_cycles WHERE id = ?",
            (cycle_id,),
        ).fetchone()
        if not row:
            return
        balance_at_open = float(row["balance_at_open"])
        pnl_usdt = balance_at_close - balance_at_open
        pnl_pct = (pnl_usdt / balance_at_open * 100) if balance_at_open else 0.0
        conn.execute(
            """
            UPDATE trade_cycles
            SET balance_at_close = ?, pnl_usdt = ?, pnl_pct = ?, closed_at = ?, status = 'closed'
            WHERE id = ?
            """,
            (balance_at_close, pnl_usdt, pnl_pct, _utc_now(), cycle_id),
        )


def get_open_trade_cycle(symbol: str, side: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM trade_cycles
            WHERE symbol = ? AND side = ? AND status = 'open'
            ORDER BY id DESC LIMIT 1
            """,
            (symbol, side),
        ).fetchone()


def get_open_trade_cycle_for_symbol(symbol: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM trade_cycles
            WHERE symbol = ? AND status = 'open'
            ORDER BY id DESC LIMIT 1
            """,
            (symbol,),
        ).fetchone()


def get_all_trade_cycles(limit: int = 100) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM trade_cycles
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()


@dataclass
class OpenCycleRef:
    cycle_id: int | None = None
    side: str | None = None

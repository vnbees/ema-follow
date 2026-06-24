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

            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profit_takes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                taken_at TEXT NOT NULL,
                baseline_before REAL NOT NULL,
                equity_after REAL NOT NULL,
                pnl_usdt REAL NOT NULL,
                pnl_pct REAL NOT NULL
            );
            """
        )
        _migrate_watchlist(conn)
        _migrate_entries_cycle_id(conn)
        _migrate_profit_takes(conn)
        _migrate_profit_take_trigger_type(conn)
        _migrate_ichimoku_trades(conn)


def _migrate_profit_take_trigger_type(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(profit_takes)").fetchall()}
    if "trigger_type" in cols:
        return
    conn.execute(
        "ALTER TABLE profit_takes ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'target'"
    )


def _migrate_profit_takes(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='profit_takes'"
    ).fetchone()
    if row:
        return
    conn.execute(
        """
        CREATE TABLE profit_takes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            taken_at TEXT NOT NULL,
            baseline_before REAL NOT NULL,
            equity_after REAL NOT NULL,
            pnl_usdt REAL NOT NULL,
            pnl_pct REAL NOT NULL
        )
        """
    )


def _migrate_entries_cycle_id(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()}
    if "cycle_id" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN cycle_id INTEGER")


def _migrate_watchlist(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    if count > 0:
        return
    row = conn.execute("SELECT value FROM settings WHERE key = 'symbol'").fetchone()
    symbol = (row["value"] if row else DEFAULT_SYMBOL).upper()
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (symbol, created_at) VALUES (?, ?)",
        (symbol, _utc_now()),
    )
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


def get_baseline_equity() -> float | None:
    raw = get_setting("baseline_equity", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def set_baseline_equity(equity: float) -> None:
    set_setting("baseline_equity", str(equity))
    set_setting("baseline_updated_at", _utc_now())


def insert_profit_take(
    baseline_before: float,
    equity_after: float,
    pnl_usdt: float,
    pnl_pct: float,
    trigger_type: str = "target",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO profit_takes (
                taken_at, baseline_before, equity_after, pnl_usdt, pnl_pct, trigger_type
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_utc_now(), baseline_before, equity_after, pnl_usdt, pnl_pct, trigger_type),
        )
        return int(cursor.lastrowid)


def get_profit_takes(limit: int = 200) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM profit_takes ORDER BY taken_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_baseline_updated_at() -> str:
    return get_setting("baseline_updated_at", "")


def init_baseline_if_missing(equity: float) -> bool:
    if get_baseline_equity() is not None:
        return False
    set_baseline_equity(equity)
    return True


def get_open_trade_cycles_all() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM trade_cycles WHERE status = 'open' ORDER BY id ASC
            """,
        ).fetchall()


def cancel_pending_entries(symbol: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE entries SET status = 'cancelled' WHERE symbol = ? AND status = 'pending'",
            (symbol,),
        )


def get_symbol() -> str:
    symbols = get_symbols()
    return symbols[0] if symbols else DEFAULT_SYMBOL


def get_symbols() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT symbol FROM watchlist ORDER BY created_at ASC").fetchall()
    return [row["symbol"] for row in rows]


def has_symbols() -> bool:
    return len(get_symbols()) > 0


def add_symbol(symbol: str) -> bool:
    symbol = symbol.upper().strip()
    if not symbol:
        return False
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO watchlist (symbol, created_at) VALUES (?, ?)",
                (symbol, _utc_now()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_symbol(symbol: str) -> bool:
    symbol = symbol.upper().strip()
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
        return cursor.rowcount > 0


def insert_entry(
    symbol: str,
    side: str,
    order_id: str,
    client_oid: str,
    price: float,
    size: float,
    status: str = "pending",
    cycle_id: int | None = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO entries (symbol, side, order_id, client_oid, price, size, status, created_at, cycle_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, side, order_id, client_oid, price, size, status, _utc_now(), cycle_id),
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


def update_entry_by_order_id(
    order_id: str,
    status: str,
    filled: bool = False,
    fill_price: float | None = None,
) -> None:
    with get_connection() as conn:
        if filled:
            if fill_price is not None and fill_price > 0:
                conn.execute(
                    "UPDATE entries SET status = ?, price = ?, filled_at = ? WHERE order_id = ?",
                    (status, fill_price, _utc_now(), order_id),
                )
            else:
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


def get_filled_entries(
    symbol: str,
    side: str,
    cycle_id: int | None = None,
) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if cycle_id is not None:
            return conn.execute(
                """
                SELECT * FROM entries
                WHERE symbol = ? AND side = ? AND status = 'filled' AND cycle_id = ?
                ORDER BY id ASC
                """,
                (symbol, side, cycle_id),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM entries
            WHERE symbol = ? AND side = ? AND status = 'filled'
            ORDER BY id ASC
            """,
            (symbol, side),
        ).fetchall()


def compute_avg_entry_price(
    symbol: str,
    side: str,
    cycle_id: int | None = None,
) -> float | None:
    rows = get_filled_entries(symbol, side, cycle_id=cycle_id)
    if not rows:
        return None
    total_value = sum(float(row["price"]) * float(row["size"]) for row in rows)
    total_size = sum(float(row["size"]) for row in rows)
    if total_size <= 0:
        return None
    return total_value / total_size


def assign_entries_to_cycle(symbol: str, side: str, cycle_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE entries SET cycle_id = ?
            WHERE symbol = ? AND side = ? AND cycle_id IS NULL
            AND status IN ('pending', 'filled')
            """,
            (cycle_id, symbol, side),
        )


def archive_filled_entries(
    symbol: str,
    side: str | None = None,
    cycle_id: int | None = None,
) -> None:
    with get_connection() as conn:
        if cycle_id is not None:
            conn.execute(
                """
                UPDATE entries SET status = 'archived'
                WHERE symbol = ? AND cycle_id = ? AND status = 'filled'
                """,
                (symbol, cycle_id),
            )
        elif side is not None:
            conn.execute(
                """
                UPDATE entries SET status = 'archived'
                WHERE symbol = ? AND side = ? AND status = 'filled'
                """,
                (symbol, side),
            )


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


def _migrate_ichimoku_trades(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ichimoku_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            risk_distance REAL NOT NULL,
            tp1_price REAL NOT NULL,
            partial_taken INTEGER NOT NULL DEFAULT 0,
            trigger_type TEXT,
            position_size REAL,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ichimoku_trades_open_symbol
        ON ichimoku_trades(symbol) WHERE status = 'open'
        """
    )


def insert_ichimoku_trade(
    symbol: str,
    side: str,
    entry_price: float,
    stop_price: float,
    risk_distance: float,
    tp1_price: float,
    trigger_type: str = "",
    position_size: float | None = None,
) -> int:
    now = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO ichimoku_trades (
                symbol, side, status, entry_price, stop_price, risk_distance,
                tp1_price, partial_taken, trigger_type, position_size,
                opened_at, updated_at
            )
            VALUES (?, ?, 'open', ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                side,
                entry_price,
                stop_price,
                risk_distance,
                tp1_price,
                trigger_type,
                position_size,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def update_ichimoku_trade(
    symbol: str,
    *,
    entry_price: float | None = None,
    stop_price: float | None = None,
    risk_distance: float | None = None,
    tp1_price: float | None = None,
    partial_taken: bool | None = None,
    position_size: float | None = None,
) -> None:
    fields: list[str] = []
    values: list[object] = []
    if entry_price is not None:
        fields.append("entry_price = ?")
        values.append(entry_price)
    if stop_price is not None:
        fields.append("stop_price = ?")
        values.append(stop_price)
    if risk_distance is not None:
        fields.append("risk_distance = ?")
        values.append(risk_distance)
    if tp1_price is not None:
        fields.append("tp1_price = ?")
        values.append(tp1_price)
    if partial_taken is not None:
        fields.append("partial_taken = ?")
        values.append(1 if partial_taken else 0)
    if position_size is not None:
        fields.append("position_size = ?")
        values.append(position_size)
    if not fields:
        return
    fields.append("updated_at = ?")
    values.append(_utc_now())
    values.append(symbol.upper())
    with get_connection() as conn:
        conn.execute(
            f"UPDATE ichimoku_trades SET {', '.join(fields)} WHERE symbol = ? AND status = 'open'",
            values,
        )


def close_ichimoku_trade(symbol: str) -> None:
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE ichimoku_trades
            SET status = 'closed', closed_at = ?, updated_at = ?
            WHERE symbol = ? AND status = 'open'
            """,
            (now, now, symbol.upper()),
        )


def get_open_ichimoku_trades() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM ichimoku_trades
            WHERE status = 'open'
            ORDER BY opened_at ASC
            """,
        ).fetchall()


def get_open_ichimoku_trade(symbol: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM ichimoku_trades
            WHERE symbol = ? AND status = 'open'
            ORDER BY id DESC LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()


@dataclass
class OpenCycleRef:
    cycle_id: int | None = None
    side: str | None = None

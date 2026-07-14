import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
        _migrate_ichimoku_close_reason(conn)
        _migrate_supertrend_trades(conn)
        _migrate_rsi_trades(conn)
        _migrate_rsi_trades_dca_count(conn)
        _migrate_rsi_trades_sizing_pnl(conn)
        _migrate_manual_hold_symbols(conn)
        _migrate_rsi_pair_lots(conn)
        _migrate_equity_snapshots(conn)
        _migrate_spot_transfer_tables(conn)


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


def _migrate_ichimoku_close_reason(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ichimoku_trades)").fetchall()}
    if "close_reason" not in cols:
        conn.execute("ALTER TABLE ichimoku_trades ADD COLUMN close_reason TEXT")


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


def close_ichimoku_trade(symbol: str, close_reason: str = "") -> None:
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE ichimoku_trades
            SET status = 'closed', closed_at = ?, updated_at = ?, close_reason = ?
            WHERE symbol = ? AND status = 'open'
            """,
            (now, now, close_reason or None, symbol.upper()),
        )


def get_recent_closed_ichimoku_trades(limit: int = 30) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM ichimoku_trades
            WHERE status = 'closed'
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_ichimoku_trades_for_dashboard(closed_limit: int = 30) -> list[sqlite3.Row]:
    open_rows = get_open_ichimoku_trades()
    closed_rows = get_recent_closed_ichimoku_trades(closed_limit)
    return list(open_rows) + list(closed_rows)


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


def _migrate_supertrend_trades(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS supertrend_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            entry_price REAL NOT NULL,
            position_size REAL,
            trend_5m_entry TEXT,
            trend_1h_entry TEXT,
            entry_trigger TEXT,
            close_reason TEXT,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_supertrend_trades_open_symbol
        ON supertrend_trades(symbol) WHERE status = 'open'
        """
    )


def insert_supertrend_trade(
    symbol: str,
    side: str,
    entry_price: float,
    trend_5m_entry: str,
    trend_1h_entry: str,
    entry_trigger: str = "5m_flip",
    position_size: float | None = None,
) -> int:
    now = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO supertrend_trades (
                symbol, side, status, entry_price, position_size,
                trend_5m_entry, trend_1h_entry, entry_trigger,
                opened_at, updated_at
            )
            VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                side,
                entry_price,
                position_size,
                trend_5m_entry,
                trend_1h_entry,
                entry_trigger,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def update_supertrend_trade(
    symbol: str,
    *,
    entry_price: float | None = None,
    position_size: float | None = None,
) -> None:
    fields: list[str] = []
    values: list[object] = []
    if entry_price is not None:
        fields.append("entry_price = ?")
        values.append(entry_price)
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
            f"UPDATE supertrend_trades SET {', '.join(fields)} WHERE symbol = ? AND status = 'open'",
            values,
        )


def close_supertrend_trade(symbol: str, close_reason: str = "") -> None:
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE supertrend_trades
            SET status = 'closed', closed_at = ?, updated_at = ?, close_reason = ?
            WHERE symbol = ? AND status = 'open'
            """,
            (now, now, close_reason or None, symbol.upper()),
        )


def get_open_supertrend_trades() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM supertrend_trades
            WHERE status = 'open'
            ORDER BY opened_at ASC
            """,
        ).fetchall()


def get_open_supertrend_trade(symbol: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM supertrend_trades
            WHERE symbol = ? AND status = 'open'
            ORDER BY id DESC LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()


def get_recent_closed_supertrend_trades(limit: int = 50) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM supertrend_trades
            WHERE status = 'closed'
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_supertrend_trades_for_dashboard(closed_limit: int = 50) -> list[sqlite3.Row]:
    return list(get_open_supertrend_trades()) + list(get_recent_closed_supertrend_trades(closed_limit))


def _migrate_rsi_trades(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rsi_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            entry_price REAL NOT NULL,
            position_size REAL,
            rsi_entry REAL,
            entry_trigger TEXT,
            close_reason TEXT,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_rsi_trades_open_symbol
        ON rsi_trades(symbol) WHERE status = 'open'
        """
    )


def _migrate_rsi_trades_dca_count(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(rsi_trades)").fetchall()}
    if "dca_count" not in cols:
        conn.execute(
            "ALTER TABLE rsi_trades ADD COLUMN dca_count INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_rsi_trades_sizing_pnl(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(rsi_trades)").fetchall()}
    if "margin_usdt" not in cols:
        conn.execute("ALTER TABLE rsi_trades ADD COLUMN margin_usdt REAL")
    if "realized_pnl_usdt" not in cols:
        conn.execute("ALTER TABLE rsi_trades ADD COLUMN realized_pnl_usdt REAL")
    if "close_price" not in cols:
        conn.execute("ALTER TABLE rsi_trades ADD COLUMN close_price REAL")
    conn.execute(
        "UPDATE rsi_trades SET margin_usdt = 5 WHERE margin_usdt IS NULL"
    )


def _migrate_manual_hold_symbols(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_hold_symbols (
            symbol TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO manual_hold_symbols (symbol, created_at) VALUES (?, ?)",
        ("VELVETUSDT", _utc_now()),
    )


def get_manual_hold_symbols() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT symbol FROM manual_hold_symbols ORDER BY created_at ASC"
        ).fetchall()
    return [row["symbol"] for row in rows]


def get_manual_hold_set() -> set[str]:
    return set(get_manual_hold_symbols())


def is_manual_hold(symbol: str) -> bool:
    symbol = symbol.upper().strip()
    if not symbol:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM manual_hold_symbols WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    return row is not None


def add_manual_hold_symbol(symbol: str) -> bool:
    symbol = symbol.upper().strip()
    if not symbol:
        return False
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO manual_hold_symbols (symbol, created_at) VALUES (?, ?)",
                (symbol, _utc_now()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_manual_hold_symbol(symbol: str) -> bool:
    symbol = symbol.upper().strip()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM manual_hold_symbols WHERE symbol = ?",
            (symbol,),
        )
        return cursor.rowcount > 0


def remove_manual_hold_symbol(symbol: str) -> bool:
    symbol = symbol.upper().strip()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM manual_hold_symbols WHERE symbol = ?",
            (symbol,),
        )
        return cursor.rowcount > 0


def _migrate_rsi_pair_lots(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rsi_pair_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            long_size REAL NOT NULL DEFAULT 0,
            long_entry REAL NOT NULL DEFAULT 0,
            long_status TEXT NOT NULL DEFAULT 'open',
            short_size REAL NOT NULL DEFAULT 0,
            short_entry REAL NOT NULL DEFAULT 0,
            short_status TEXT NOT NULL DEFAULT 'open',
            margin_usdt REAL,
            entry_trigger TEXT,
            opened_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            long_closed_at TEXT,
            short_closed_at TEXT,
            long_realized_pnl_usdt REAL,
            short_realized_pnl_usdt REAL,
            long_close_price REAL,
            short_close_price REAL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rsi_pair_lots_symbol_open
        ON rsi_pair_lots(symbol, opened_at)
        """
    )


def insert_pair_lot(
    symbol: str,
    *,
    long_size: float,
    long_entry: float,
    short_size: float,
    short_entry: float,
    margin_usdt: float,
    entry_trigger: str,
) -> int:
    now = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO rsi_pair_lots (
                symbol, long_size, long_entry, long_status,
                short_size, short_entry, short_status,
                margin_usdt, entry_trigger, opened_at, updated_at
            )
            VALUES (?, ?, ?, 'open', ?, ?, 'open', ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                long_size,
                long_entry,
                short_size,
                short_entry,
                margin_usdt,
                entry_trigger,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def get_open_pair_lots(symbol: str | None = None) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if symbol:
            return conn.execute(
                """
                SELECT * FROM rsi_pair_lots
                WHERE symbol = ?
                  AND (long_status = 'open' OR short_status = 'open')
                ORDER BY opened_at ASC
                """,
                (symbol.upper(),),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM rsi_pair_lots
            WHERE long_status = 'open' OR short_status = 'open'
            ORDER BY symbol ASC, opened_at ASC
            """
        ).fetchall()


def get_all_open_pair_lots() -> list[sqlite3.Row]:
    return get_open_pair_lots(None)


def get_recent_pair_lots(limit: int = 100) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM rsi_pair_lots
            ORDER BY opened_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_pair_lots_for_dashboard(limit: int = 80) -> list[sqlite3.Row]:
    open_rows = get_all_open_pair_lots()
    open_ids = {row["id"] for row in open_rows}
    recent = get_recent_pair_lots(limit)
    rows = list(open_rows)
    for row in recent:
        if row["id"] not in open_ids:
            rows.append(row)
    rows.sort(key=lambda r: (r["symbol"], r["opened_at"]), reverse=False)
    return rows


def close_lot_side(
    lot_id: int,
    side: str,
    *,
    realized_pnl_usdt: float | None = None,
    close_price: float | None = None,
) -> None:
    side = side.lower()
    now = _utc_now()
    if side == "long":
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE rsi_pair_lots
                SET long_status = 'closed', long_closed_at = ?, updated_at = ?,
                    long_realized_pnl_usdt = ?, long_close_price = ?
                WHERE id = ?
                """,
                (now, now, realized_pnl_usdt, close_price, lot_id),
            )
        return
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE rsi_pair_lots
            SET short_status = 'closed', short_closed_at = ?, updated_at = ?,
                short_realized_pnl_usdt = ?, short_close_price = ?
            WHERE id = ?
            """,
            (now, now, realized_pnl_usdt, close_price, lot_id),
        )


def close_all_lot_sides(
    symbol: str,
    side: str,
    *,
    close_price: float | None = None,
    mark: float | None = None,
) -> None:
    side = side.lower()
    price = close_price if close_price is not None else mark
    for lot in get_open_pair_lots(symbol.upper()):
        status_col = "long_status" if side == "long" else "short_status"
        if lot[status_col] != "open":
            continue
        entry = float(lot["long_entry"] if side == "long" else lot["short_entry"])
        size = float(lot["long_size"] if side == "long" else lot["short_size"])
        realized = None
        if price and price > 0 and entry > 0 and size > 0:
            if side == "long":
                realized = (price - entry) * size
            else:
                realized = (entry - price) * size
        close_lot_side(
            int(lot["id"]),
            side,
            realized_pnl_usdt=realized,
            close_price=price,
        )


def count_open_symbols() -> int:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT symbol) AS cnt FROM rsi_pair_lots
            WHERE long_status = 'open' OR short_status = 'open'
            """
        ).fetchone()
    return int(row["cnt"]) if row else 0


def count_open_legs() -> int:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              SUM(CASE WHEN long_status = 'open' THEN 1 ELSE 0 END)
            + SUM(CASE WHEN short_status = 'open' THEN 1 ELSE 0 END) AS cnt
            FROM rsi_pair_lots
            WHERE long_status = 'open' OR short_status = 'open'
            """
        ).fetchone()
    return int(row["cnt"] or 0) if row else 0


def symbol_has_open_lots(symbol: str) -> bool:
    return len(get_open_pair_lots(symbol)) > 0


def get_recent_leg_events(limit: int = 10) -> list[sqlite3.Row]:
    """Recent open/close leg events for dashboard (newest first)."""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                event_at,
                event_type,
                symbol,
                side,
                lot_id,
                size,
                entry,
                close_price,
                realized_pnl_usdt,
                entry_trigger
            FROM (
                SELECT
                    opened_at AS event_at,
                    'open' AS event_type,
                    symbol,
                    'long' AS side,
                    id AS lot_id,
                    long_size AS size,
                    long_entry AS entry,
                    NULL AS close_price,
                    NULL AS realized_pnl_usdt,
                    entry_trigger
                FROM rsi_pair_lots
                UNION ALL
                SELECT
                    opened_at,
                    'open',
                    symbol,
                    'short',
                    id,
                    short_size,
                    short_entry,
                    NULL,
                    NULL,
                    entry_trigger
                FROM rsi_pair_lots
                UNION ALL
                SELECT
                    long_closed_at,
                    'close',
                    symbol,
                    'long',
                    id,
                    long_size,
                    long_entry,
                    long_close_price,
                    long_realized_pnl_usdt,
                    entry_trigger
                FROM rsi_pair_lots
                WHERE long_closed_at IS NOT NULL
                UNION ALL
                SELECT
                    short_closed_at,
                    'close',
                    symbol,
                    'short',
                    id,
                    short_size,
                    short_entry,
                    short_close_price,
                    short_realized_pnl_usdt,
                    entry_trigger
                FROM rsi_pair_lots
                WHERE short_closed_at IS NOT NULL
            )
            ORDER BY event_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_closed_lot_side_events() -> list[sqlite3.Row]:
    """One row per closed leg for PnL calendar and history."""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                symbol,
                side,
                closed_at,
                realized_pnl_usdt,
                close_price,
                entry,
                size,
                margin_usdt,
                entry_trigger,
                lot_id
            FROM (
                SELECT
                    symbol,
                    'long' AS side,
                    long_closed_at AS closed_at,
                    long_realized_pnl_usdt AS realized_pnl_usdt,
                    long_close_price AS close_price,
                    long_entry AS entry,
                    long_size AS size,
                    margin_usdt,
                    entry_trigger,
                    id AS lot_id
                FROM rsi_pair_lots
                WHERE long_closed_at IS NOT NULL
                UNION ALL
                SELECT
                    symbol,
                    'short' AS side,
                    short_closed_at AS closed_at,
                    short_realized_pnl_usdt AS realized_pnl_usdt,
                    short_close_price AS close_price,
                    short_entry AS entry,
                    short_size AS size,
                    margin_usdt,
                    entry_trigger,
                    id AS lot_id
                FROM rsi_pair_lots
                WHERE short_closed_at IS NOT NULL
            )
            ORDER BY closed_at DESC
            """
        ).fetchall()


def get_closed_realized_from_lots() -> tuple[float, int, int]:
    """Return (total realized USDT incl. estimates, exact PnL leg count, total closed legs)."""
    from src.config import PAIR_PROFIT_TARGET_PCT
    from src.pnl import estimate_tp_pnl_usdt, leg_realized_pnl

    total = 0.0
    exact_count = 0
    total_legs = 0
    for row in get_closed_lot_side_events():
        total_legs += 1
        entry = float(row["entry"] or 0)
        size = float(row["size"] or 0)
        pnl = leg_realized_pnl(
            row["side"],
            entry,
            size,
            realized_pnl_usdt=row["realized_pnl_usdt"],
            close_price=row["close_price"],
        )
        if pnl is not None:
            exact_count += 1
        elif entry > 0 and size > 0:
            pnl = estimate_tp_pnl_usdt(entry, size, PAIR_PROFIT_TARGET_PCT)
        if pnl is not None:
            total += pnl
    return total, exact_count, total_legs


def insert_rsi_trade(
    symbol: str,
    side: str,
    entry_price: float,
    rsi_entry: float,
    entry_trigger: str = "rsi_cross_25",
    position_size: float | None = None,
    margin_usdt: float | None = None,
) -> int:
    now = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO rsi_trades (
                symbol, side, status, entry_price, position_size,
                rsi_entry, entry_trigger, margin_usdt, opened_at, updated_at
            )
            VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                side,
                entry_price,
                position_size,
                rsi_entry,
                entry_trigger,
                margin_usdt,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def update_rsi_trade(
    symbol: str,
    *,
    entry_price: float | None = None,
    position_size: float | None = None,
    rsi_entry: float | None = None,
    entry_trigger: str | None = None,
    dca_count: int | None = None,
) -> None:
    fields: list[str] = []
    values: list[object] = []
    if entry_price is not None:
        fields.append("entry_price = ?")
        values.append(entry_price)
    if position_size is not None:
        fields.append("position_size = ?")
        values.append(position_size)
    if rsi_entry is not None:
        fields.append("rsi_entry = ?")
        values.append(rsi_entry)
    if entry_trigger is not None:
        fields.append("entry_trigger = ?")
        values.append(entry_trigger)
    if dca_count is not None:
        fields.append("dca_count = ?")
        values.append(dca_count)
    if not fields:
        return
    fields.append("updated_at = ?")
    values.append(_utc_now())
    values.append(symbol.upper())
    with get_connection() as conn:
        conn.execute(
            f"UPDATE rsi_trades SET {', '.join(fields)} WHERE symbol = ? AND status = 'open'",
            values,
        )


def close_rsi_trade(
    symbol: str,
    close_reason: str = "",
    *,
    realized_pnl_usdt: float | None = None,
    close_price: float | None = None,
) -> None:
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE rsi_trades
            SET status = 'closed', closed_at = ?, updated_at = ?, close_reason = ?,
                realized_pnl_usdt = ?, close_price = ?
            WHERE symbol = ? AND status = 'open'
            """,
            (
                now,
                now,
                close_reason or None,
                realized_pnl_usdt,
                close_price,
                symbol.upper(),
            ),
        )


def get_open_rsi_trades() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM rsi_trades
            WHERE status = 'open'
            ORDER BY opened_at ASC
            """,
        ).fetchall()


def get_open_rsi_trade(symbol: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM rsi_trades
            WHERE symbol = ? AND status = 'open'
            ORDER BY id DESC LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()


def get_recent_closed_rsi_trades(limit: int = 50) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM rsi_trades
            WHERE status = 'closed'
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_all_closed_rsi_trades() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM rsi_trades
            WHERE status = 'closed' AND closed_at IS NOT NULL
            ORDER BY closed_at DESC
            """,
        ).fetchall()


def get_rsi_trades_for_dashboard(closed_limit: int = 50) -> list[sqlite3.Row]:
    return list(get_open_rsi_trades()) + list(get_recent_closed_rsi_trades(closed_limit))


def get_rsi_closed_realized_summary(closed_limit: int = 50) -> tuple[float, int]:
    """Sum realized PnL from closed trades that have a recorded value."""
    rows = get_recent_closed_rsi_trades(closed_limit)
    total = 0.0
    count = 0
    for row in rows:
        if "realized_pnl_usdt" in row.keys() and row["realized_pnl_usdt"] is not None:
            total += float(row["realized_pnl_usdt"])
            count += 1
    return total, count


_EQUITY_SNAPSHOT_PRUNE_EVERY = 100
_equity_snapshot_insert_count = 0


def _migrate_equity_snapshots(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            equity REAL NOT NULL,
            available REAL NOT NULL,
            maint_margin_pct REAL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_equity_snapshots_recorded_at
        ON equity_snapshots(recorded_at)
        """
    )


def insert_equity_snapshot(
    equity: float,
    available: float,
    *,
    maint_margin_pct: float | None = None,
) -> int:
    global _equity_snapshot_insert_count
    now = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO equity_snapshots (recorded_at, equity, available, maint_margin_pct)
            VALUES (?, ?, ?, ?)
            """,
            (now, equity, available, maint_margin_pct),
        )
        row_id = int(cursor.lastrowid)
    _equity_snapshot_insert_count += 1
    if _equity_snapshot_insert_count % _EQUITY_SNAPSHOT_PRUNE_EVERY == 0:
        prune_equity_snapshots()
    return row_id


def get_equity_snapshots(
    since: datetime | None = None,
    *,
    limit: int = 10000,
) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if since is not None:
            since_iso = since.astimezone(timezone.utc).isoformat()
            return conn.execute(
                """
                SELECT * FROM equity_snapshots
                WHERE recorded_at >= ?
                ORDER BY recorded_at ASC
                LIMIT ?
                """,
                (since_iso, limit),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM equity_snapshots
            ORDER BY recorded_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def prune_equity_snapshots(*, older_than_days: int = 90) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM equity_snapshots WHERE recorded_at < ?",
            (cutoff,),
        )
        return int(cursor.rowcount)


_SPOT_SNAPSHOT_PRUNE_EVERY = 100
_spot_snapshot_insert_count = 0


def _migrate_spot_transfer_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spot_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transfer_date TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            tran_id TEXT,
            available_before REAL,
            spot_after REAL,
            legs_closed INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_spot_transfers_date_status
        ON spot_transfers(transfer_date, status)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spot_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            balance REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_spot_snapshots_recorded_at
        ON spot_snapshots(recorded_at)
        """
    )


def insert_spot_transfer(
    *,
    transfer_date: str,
    amount: float,
    status: str,
    tran_id: str | None = None,
    available_before: float | None = None,
    spot_after: float | None = None,
    legs_closed: int = 0,
    error: str | None = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO spot_transfers (
                transfer_date, amount, status, tran_id,
                available_before, spot_after, legs_closed, error, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transfer_date,
                amount,
                status,
                tran_id,
                available_before,
                spot_after,
                legs_closed,
                error,
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def get_spot_transfers(limit: int = 50) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM spot_transfers
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def has_successful_transfer_on_date(transfer_date: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM spot_transfers
            WHERE transfer_date = ? AND status = 'success'
            LIMIT 1
            """,
            (transfer_date,),
        ).fetchone()
        return row is not None


def get_setting_bool(key: str, default: bool) -> bool:
    raw = get_setting(key, "")
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def set_setting_bool(key: str, value: bool) -> None:
    set_setting(key, "true" if value else "false")


def get_spot_transfer_amount(default: float) -> float:
    raw = get_setting("spot_transfer_amount", "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def set_spot_transfer_amount(amount: float) -> None:
    set_setting("spot_transfer_amount", str(amount))


def is_spot_transfer_enabled(default: bool = True) -> bool:
    return get_setting_bool("spot_transfer_enabled", default)


def set_spot_transfer_enabled(enabled: bool) -> None:
    set_setting_bool("spot_transfer_enabled", enabled)


def insert_spot_snapshot(balance: float) -> int:
    global _spot_snapshot_insert_count
    now = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO spot_snapshots (recorded_at, balance)
            VALUES (?, ?)
            """,
            (now, balance),
        )
        row_id = int(cursor.lastrowid)
    _spot_snapshot_insert_count += 1
    if _spot_snapshot_insert_count % _SPOT_SNAPSHOT_PRUNE_EVERY == 0:
        prune_spot_snapshots()
    return row_id


def get_spot_snapshots(
    since: datetime | None = None,
    *,
    limit: int = 10000,
) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if since is not None:
            since_iso = since.astimezone(timezone.utc).isoformat()
            return conn.execute(
                """
                SELECT * FROM spot_snapshots
                WHERE recorded_at >= ?
                ORDER BY recorded_at ASC
                LIMIT ?
                """,
                (since_iso, limit),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM spot_snapshots
            ORDER BY recorded_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def prune_spot_snapshots(*, older_than_days: int = 90) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM spot_snapshots WHERE recorded_at < ?",
            (cutoff,),
        )
        return int(cursor.rowcount)


def clear_dashboard_history(*, reset_baseline: bool = True) -> dict[str, int]:
    """Delete RSI lot/trade history shown on the dashboard. Keeps manual_hold_symbols."""
    tables = (
        "rsi_pair_lots",
        "rsi_trades",
        "profit_takes",
        "entries",
        "trade_cycles",
        "equity_snapshots",
        "spot_transfers",
        "spot_snapshots",
    )
    counts: dict[str, int] = {}
    with get_connection() as conn:
        for table in tables:
            row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()
            counts[table] = int(row["cnt"]) if row else 0
            conn.execute(f"DELETE FROM {table}")
        if reset_baseline:
            conn.execute(
                "DELETE FROM settings WHERE key IN ('baseline_equity', 'baseline_updated_at')"
            )
    return counts


@dataclass
class OpenCycleRef:
    cycle_id: int | None = None
    side: str | None = None

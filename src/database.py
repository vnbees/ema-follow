import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from src.config import DATABASE_PATH

_equity_snapshot_insert_count = 0
_EQUITY_SNAPSHOT_PRUNE_EVERY = 100


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

            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                equity REAL NOT NULL,
                available REAL NOT NULL,
                maint_margin_pct REAL
            );
            """
        )
    from src.ema_rsi import store

    store.ensure_schema()


def get_setting(key: str, default: str = "") -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_setting_bool(key: str, default: bool) -> bool:
    raw = get_setting(key, "")
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def set_setting_bool(key: str, value: bool) -> None:
    set_setting(key, "true" if value else "false")


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


def get_baseline_updated_at() -> str:
    return get_setting("baseline_updated_at", "")


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


def clear_dashboard_history(*, reset_baseline: bool = True) -> dict[str, int]:
    """Delete EMA-RSI trade history and equity chart data."""
    tables = ("ema_rsi_trades", "ema_rsi_seen_signals", "equity_snapshots")
    counts: dict[str, int] = {}
    with get_connection() as conn:
        for table in tables:
            try:
                row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()
                counts[table] = int(row["cnt"]) if row else 0
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                counts[table] = 0
        if reset_baseline:
            conn.execute(
                "DELETE FROM settings WHERE key IN ('baseline_equity', 'baseline_updated_at')"
            )
    return counts

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
        _migrate_spot_transfer_tables(conn)
    from src.donchian import store

    store.ensure_schema()


def _migrate_spot_transfer_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS spot_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transfer_date TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            tran_id TEXT,
            available_before REAL,
            spot_after REAL,
            sod_equity REAL,
            eod_equity REAL,
            day_pnl REAL,
            dd_pct REAL,
            peak_equity REAL,
            reason TEXT,
            error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_spot_transfers_date_status
            ON spot_transfers(transfer_date, status);
        """
    )
    # Additive columns for DBs created with older schema
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(spot_transfers)").fetchall()}
    for col, decl in (
        ("sod_equity", "REAL"),
        ("eod_equity", "REAL"),
        ("day_pnl", "REAL"),
        ("dd_pct", "REAL"),
        ("peak_equity", "REAL"),
        ("reason", "TEXT"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE spot_transfers ADD COLUMN {col} {decl}")


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
    """Delete Donchian trade history and equity chart data."""
    tables = (
        "donchian_lots",
        "donchian_state",
        "donchian_skips",
        "equity_snapshots",
    )
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


def insert_spot_transfer(
    *,
    transfer_date: str,
    amount: float,
    status: str,
    tran_id: str | None = None,
    available_before: float | None = None,
    spot_after: float | None = None,
    sod_equity: float | None = None,
    eod_equity: float | None = None,
    day_pnl: float | None = None,
    dd_pct: float | None = None,
    peak_equity: float | None = None,
    reason: str | None = None,
    error: str | None = None,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO spot_transfers (
                transfer_date, amount, status, tran_id,
                available_before, spot_after,
                sod_equity, eod_equity, day_pnl, dd_pct, peak_equity,
                reason, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transfer_date,
                amount,
                status,
                tran_id,
                available_before,
                spot_after,
                sod_equity,
                eod_equity,
                day_pnl,
                dd_pct,
                peak_equity,
                reason,
                error,
                _utc_now(),
            ),
        )
        return int(cur.lastrowid)


def get_spot_transfers(limit: int = 50) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM spot_transfers
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_spot_transfers_paged(
    *,
    since_date: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[sqlite3.Row], int]:
    """Paginated spot_transfers, newest first. since_date = YYYY-MM-DD inclusive."""
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    offset = (page - 1) * page_size
    with get_connection() as conn:
        if since_date:
            total_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM spot_transfers WHERE transfer_date >= ?",
                (since_date,),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT * FROM spot_transfers
                WHERE transfer_date >= ?
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (since_date, page_size, offset),
            ).fetchall()
        else:
            total_row = conn.execute("SELECT COUNT(*) AS cnt FROM spot_transfers").fetchone()
            rows = conn.execute(
                """
                SELECT * FROM spot_transfers
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ).fetchall()
        total = int(total_row["cnt"]) if total_row else 0
    return rows, total


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


def has_transfer_decision_on_date(transfer_date: str) -> bool:
    """True if we already recorded success / skipped / failed for this VN date."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM spot_transfers
            WHERE transfer_date = ? AND status IN ('success', 'skipped', 'failed')
            LIMIT 1
            """,
            (transfer_date,),
        ).fetchone()
        return row is not None


def is_spot_transfer_enabled(default: bool = True) -> bool:
    return get_setting_bool("spot_transfer_enabled", default)


def set_spot_transfer_enabled(enabled: bool) -> None:
    set_setting_bool("spot_transfer_enabled", enabled)


def _get_float_setting(key: str) -> float | None:
    raw = get_setting(key, "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def get_equity_peak() -> float | None:
    return _get_float_setting("equity_peak")


def set_equity_peak(value: float) -> None:
    set_setting("equity_peak", str(value))
    set_setting("equity_peak_updated_at", _utc_now())


def get_spot_sod_equity() -> float | None:
    return _get_float_setting("spot_sod_equity")


def set_spot_sod_equity(value: float, *, day: str | None = None) -> None:
    set_setting("spot_sod_equity", str(value))
    set_setting("spot_sod_updated_at", _utc_now())
    if day:
        set_setting("spot_sod_day", day)


def get_spot_sod_day() -> str:
    return get_setting("spot_sod_day", "")


def get_spot_manual_synced_at_ms() -> int | None:
    raw = get_setting("spot_manual_synced_at_ms", "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def set_spot_manual_synced_at_ms(ms: int) -> None:
    set_setting("spot_manual_synced_at_ms", str(int(ms)))

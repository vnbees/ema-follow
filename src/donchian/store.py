from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.database import get_connection

_lock = threading.Lock()
_VN = ZoneInfo("Asia/Ho_Chi_Minh")

REASON_TP = "TP_BAND"
REASON_EOD = "EOD_OPEN"

REASON_LABELS = {
    REASON_TP: "Chạm Donchian band",
    REASON_EOD: "Đóng thủ công / EOD",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    ddl = """
        CREATE TABLE IF NOT EXISTS donchian_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            trend TEXT NOT NULL,
            trend_ts INTEGER,
            status TEXT NOT NULL DEFAULT 'open',
            entry_ts INTEGER,
            entry_px REAL NOT NULL,
            tp_band REAL NOT NULL,
            size REAL NOT NULL,
            margin_usdt REAL,
            notional_usdt REAL,
            entry_order_id TEXT,
            close_order_id TEXT,
            close_ts INTEGER,
            close_px REAL,
            close_reason TEXT,
            pnl_usdt REAL,
            fee_open_usdt REAL,
            fee_close_usdt REAL,
            body_atr REAL,
            pot_rr REAL,
            size_mult REAL,
            opp_band REAL,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_donchian_lots_open
            ON donchian_lots(status, symbol);
        CREATE INDEX IF NOT EXISTS idx_donchian_lots_opened
            ON donchian_lots(opened_at);
        CREATE INDEX IF NOT EXISTS idx_donchian_lots_closed
            ON donchian_lots(closed_at);

        CREATE TABLE IF NOT EXISTS donchian_state (
            symbol TEXT PRIMARY KEY,
            trend TEXT,
            trend_ts INTEGER,
            waiting_entry INTEGER NOT NULL DEFAULT 0,
            prev_parallel INTEGER NOT NULL DEFAULT 0,
            last_processed_ts INTEGER,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS donchian_skips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """
    if conn is not None:
        conn.executescript(ddl)
        _migrate_donchian_state(conn)
        _migrate_donchian_lots(conn)
        return
    with get_connection() as owned:
        owned.executescript(ddl)
        _migrate_donchian_state(owned)
        _migrate_donchian_lots(owned)


def _migrate_donchian_state(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(donchian_state)")}
    if "last_processed_ts" not in cols:
        conn.execute("ALTER TABLE donchian_state ADD COLUMN last_processed_ts INTEGER")


def _migrate_donchian_lots(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(donchian_lots)")}
    if "close_order_id" not in cols:
        conn.execute("ALTER TABLE donchian_lots ADD COLUMN close_order_id TEXT")
    for col, decl in (
        ("body_atr", "REAL"),
        ("pot_rr", "REAL"),
        ("size_mult", "REAL"),
        ("opp_band", "REAL"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE donchian_lots ADD COLUMN {col} {decl}")


def save_state(
    symbol: str,
    *,
    trend: str | None,
    trend_ts: int | None,
    waiting_entry: bool,
    prev_parallel: bool,
    last_processed_ts: int | None = None,
) -> None:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO donchian_state (
                symbol, trend, trend_ts, waiting_entry, prev_parallel, last_processed_ts, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                trend=excluded.trend, trend_ts=excluded.trend_ts,
                waiting_entry=excluded.waiting_entry, prev_parallel=excluded.prev_parallel,
                last_processed_ts=excluded.last_processed_ts,
                updated_at=excluded.updated_at
            """,
            (
                symbol.upper(),
                trend,
                trend_ts,
                int(waiting_entry),
                int(prev_parallel),
                last_processed_ts,
                now,
            ),
        )


def load_state(symbol: str) -> dict | None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM donchian_state WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def insert_lot(
    *,
    symbol: str,
    side: str,
    trend: str,
    trend_ts: int | None,
    entry_ts: int | None,
    entry_px: float,
    tp_band: float,
    size: float,
    margin_usdt: float,
    notional_usdt: float,
    entry_order_id: str,
    fee_open_usdt: float = 0.0,
    body_atr: float | None = None,
    pot_rr: float | None = None,
    size_mult: float | None = None,
    opp_band: float | None = None,
) -> int:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO donchian_lots (
                symbol, side, trend, trend_ts, status, entry_ts, entry_px, tp_band,
                size, margin_usdt, notional_usdt, entry_order_id, fee_open_usdt,
                body_atr, pot_rr, size_mult, opp_band,
                opened_at, updated_at
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                side.lower(),
                trend,
                trend_ts,
                entry_ts,
                entry_px,
                tp_band,
                size,
                margin_usdt,
                notional_usdt,
                entry_order_id,
                float(fee_open_usdt or 0),
                body_atr,
                pot_rr,
                size_mult,
                opp_band,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def close_lot(
    lot_id: int,
    *,
    close_px: float,
    close_reason: str,
    pnl_usdt: float,
    fee_close_usdt: float = 0.0,
    close_order_id: str = "",
    close_ts: int | None = None,
) -> bool:
    now = _utc_now()
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """
            UPDATE donchian_lots
            SET status = 'closed', close_px = ?, close_reason = ?, pnl_usdt = ?,
                fee_close_usdt = ?, close_ts = ?, close_order_id = ?,
                closed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (
                close_px, close_reason, pnl_usdt, float(fee_close_usdt or 0),
                close_ts, close_order_id or "",
                now, now, lot_id,
            ),
        )
        return cur.rowcount > 0


def get_lot(lot_id: int) -> sqlite3.Row | None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        return conn.execute(
            "SELECT * FROM donchian_lots WHERE id = ?", (lot_id,)
        ).fetchone()


def get_open_lots(symbol: str | None = None) -> list[sqlite3.Row]:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        if symbol:
            return conn.execute(
                "SELECT * FROM donchian_lots WHERE status = 'open' AND symbol = ? ORDER BY id",
                (symbol.upper(),),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM donchian_lots WHERE status = 'open' ORDER BY id"
        ).fetchall()


def has_open_lot_for_symbol(symbol: str) -> bool:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT id FROM donchian_lots WHERE status = 'open' AND symbol = ? LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return row is not None


def count_open() -> int:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM donchian_lots WHERE status = 'open'"
        ).fetchone()
        return int(row["n"]) if row else 0


def count_closed() -> int:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM donchian_lots WHERE status = 'closed'"
        ).fetchone()
        return int(row["n"]) if row else 0


def sum_closed_pnl() -> float:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_usdt), 0) AS s FROM donchian_lots WHERE status = 'closed'"
        ).fetchone()
        return float(row["s"] or 0)


def list_lots_paged(
    *,
    status: str,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[sqlite3.Row], int]:
    page = max(1, page)
    page_size = min(100, max(20, page_size))
    offset = (page - 1) * page_size
    order_col = "opened_at" if status == "open" else "closed_at"
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        total_row = conn.execute(
            "SELECT COUNT(*) AS n FROM donchian_lots WHERE status = ?", (status,)
        ).fetchone()
        total = int(total_row["n"]) if total_row else 0
        rows = conn.execute(
            f"""
            SELECT * FROM donchian_lots
            WHERE status = ?
            ORDER BY {order_col} DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (status, page_size, offset),
        ).fetchall()
        return rows, total


def record_skip(symbol: str, reason: str) -> None:
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO donchian_skips (symbol, reason, created_at) VALUES (?, ?, ?)",
            (symbol.upper(), reason, _utc_now()),
        )


def _vn_day_expr(col: str) -> str:
    return f"date(replace(replace({col}, 'T', ' '), 'Z', ''), '+7 hours')"


def daily_stats(days: int = 30) -> list[dict[str, Any]]:
    days = max(1, min(90, days))
    open_expr = _vn_day_expr("opened_at")
    close_expr = _vn_day_expr("closed_at")
    with _lock, get_connection() as conn:
        ensure_schema(conn)
        opened = {
            str(r["d"]): int(r["n"])
            for r in conn.execute(
                f"""
                SELECT {open_expr} AS d, COUNT(*) AS n
                FROM donchian_lots
                WHERE opened_at IS NOT NULL
                GROUP BY d
                """
            )
        }
        closed_rows = conn.execute(
            f"""
            SELECT {close_expr} AS d, close_reason, COUNT(*) AS n,
                   COALESCE(SUM(pnl_usdt), 0) AS pnl
            FROM donchian_lots
            WHERE status = 'closed' AND closed_at IS NOT NULL
            GROUP BY d, close_reason
            """
        ).fetchall()

    from datetime import timedelta
    today = datetime.now(_VN).date()
    by_day: dict[str, dict[str, Any]] = {}
    for day, n in opened.items():
        by_day.setdefault(day, _empty_day(day))["opened"] = n
    for row in closed_rows:
        day = str(row["d"])
        rec = by_day.setdefault(day, _empty_day(day))
        rec["closed"] += int(row["n"])
        rec["pnl"] += float(row["pnl"] or 0)
        if str(row["close_reason"] or "") == REASON_TP:
            rec["tp"] += int(row["n"])

    out: list[dict[str, Any]] = []
    for offset in range(days):
        d = (today - timedelta(days=offset)).isoformat()
        out.append(by_day.get(d, _empty_day(d)))
    return out


def _empty_day(day: str) -> dict[str, Any]:
    return {"day": day, "opened": 0, "closed": 0, "tp": 0, "pnl": 0.0}

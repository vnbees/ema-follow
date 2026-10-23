"""SQLite book for Prediction bets. One open bet; martingale shared across coins and timeframes."""

from __future__ import annotations

from src.database import get_connection
from src.predict5m.config import TARGET_USDT, TIMEFRAMES
from src.predict5m.engine import Bankroll, fresh_bankroll

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predict5m_bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    stake REAL NOT NULL,
    double_step INTEGER NOT NULL,
    bar_ts INTEGER NOT NULL,
    entry_ts INTEGER NOT NULL,
    market_topic_id INTEGER,
    order_id TEXT,
    limit_px REAL,
    fee_bps REAL,
    fill_status TEXT,
    status TEXT NOT NULL,
    won INTEGER,
    pnl REAL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    timeframe TEXT NOT NULL DEFAULT '5m'
);
CREATE INDEX IF NOT EXISTS idx_predict5m_status ON predict5m_bets(status);

CREATE TABLE IF NOT EXISTS predict5m_state (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


def _tf_key(timeframe: str, name: str) -> str:
    return f"{timeframe}:{name}"


def ensure_schema() -> None:
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(predict5m_bets)").fetchall()}
        for col, decl in (
            ("order_id", "TEXT"),
            ("limit_px", "REAL"),
            ("fee_bps", "REAL"),
            ("fill_status", "TEXT"),
            ("timeframe", "TEXT NOT NULL DEFAULT '5m'"),
            ("target_usdt", "REAL"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE predict5m_bets ADD COLUMN {col} {decl}")
        conn.execute("UPDATE predict5m_bets SET timeframe = '5m' WHERE timeframe IS NULL OR timeframe = ''")
        conn.execute("DROP INDEX IF EXISTS idx_predict5m_symbol_bar")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_predict5m_tf_symbol_bar "
            "ON predict5m_bets(timeframe, symbol, bar_ts)"
        )
        legacy = {
            str(r["k"]): str(r["v"])
            for r in conn.execute("SELECT k, v FROM predict5m_state").fetchall()
        }
        for name, default in (
            ("double_step", "0"),
            ("halted", "0"),
            ("pause_until_ms", "0"),
            ("recovery_target", str(TARGET_USDT)),
            ("streak_loss", "0"),
        ):
            if name in legacy:
                continue
            seed = default
            for tf in TIMEFRAMES:
                alt = legacy.get(_tf_key(tf, name))
                if alt is not None:
                    if name == "double_step":
                        seed = str(max(int(seed or 0), int(alt or 0)))
                    elif name == "pause_until_ms":
                        seed = str(max(int(seed or 0), int(alt or 0)))
                    else:
                        seed = alt
            conn.execute(
                "INSERT OR IGNORE INTO predict5m_state(k, v) VALUES(?, ?)",
                (name, seed),
            )


def _get(k: str, default: str = "0") -> str:
    with get_connection() as conn:
        row = conn.execute("SELECT v FROM predict5m_state WHERE k = ?", (k,)).fetchone()
    return str(row["v"]) if row else default


def _set(k: str, v: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO predict5m_state(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (k, v),
        )


def double_step() -> int:
    return int(_get("double_step", "0"))


def halted() -> bool:
    return _get("halted", "0") in ("1", "true", "yes")


def pause_until_ms() -> int:
    return int(_get("pause_until_ms", "0") or 0)


def is_paused(now_ms: int) -> bool:
    until = pause_until_ms()
    return until > 0 and int(now_ms) < until


def start_pause(now_ms: int, duration_ms: int) -> int:
    until = int(now_ms) + int(duration_ms)
    _set("pause_until_ms", str(until))
    _set("double_step", "0")
    _set("halted", "0")
    set_bankroll(fresh_bankroll())
    return until


def clear_pause_if_due(now_ms: int) -> bool:
    """True once, when the 1h pause just expired."""
    until = pause_until_ms()
    if until > 0 and int(now_ms) >= until:
        _set("pause_until_ms", "0")
        _set("halted", "0")
        return True
    return False


def set_progress(step: int, is_halted: bool = False) -> None:
    _set("double_step", str(int(step)))
    _set("halted", "1" if is_halted else "0")


def bankroll() -> Bankroll:
    try:
        target = float(_get("recovery_target", str(TARGET_USDT)))
    except (TypeError, ValueError):
        target = float(TARGET_USDT)
    try:
        lost = float(_get("streak_loss", "0"))
    except (TypeError, ValueError):
        lost = 0.0
    if target <= 0:
        target = float(TARGET_USDT)
    return Bankroll(target=target, streak_loss=max(0.0, lost))


def set_bankroll(bank: Bankroll, is_halted: bool = False) -> None:
    _set("recovery_target", str(float(bank.target)))
    _set("streak_loss", str(float(bank.streak_loss)))
    _set("halted", "1" if is_halted else "0")


def open_bet() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM predict5m_bets WHERE status = 'open' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def open_bets() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM predict5m_bets WHERE status = 'open' ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def has_bar(symbol: str, bar_ts: int, timeframe: str = "5m") -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM predict5m_bets
            WHERE timeframe = ? AND symbol = ? AND bar_ts = ?
            """,
            (timeframe, symbol.upper(), int(bar_ts)),
        ).fetchone()
    return row is not None


def insert_open(
    *,
    symbol: str,
    side: str,
    stake: float,
    bar_ts: int,
    entry_ts: int,
    market_topic_id: int | None,
    double_step: int = 0,
    order_id: str | None = None,
    limit_px: float | None = None,
    fee_bps: float | None = None,
    fill_status: str | None = None,
    timeframe: str = "5m",
    target_usdt: float | None = None,
) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO predict5m_bets (
                symbol, side, stake, double_step, bar_ts, entry_ts, market_topic_id,
                status, order_id, limit_px, fee_bps, fill_status, timeframe, target_usdt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                side,
                float(stake),
                int(double_step),
                int(bar_ts),
                int(entry_ts),
                market_topic_id,
                order_id,
                None if limit_px is None else float(limit_px),
                None if fee_bps is None else float(fee_bps),
                fill_status,
                timeframe,
                None if target_usdt is None else float(target_usdt),
            ),
        )
        row = conn.execute(
            "SELECT * FROM predict5m_bets WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


def set_fill_status(bet_id: int, fill_status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE predict5m_bets SET fill_status = ? WHERE id = ?",
            (str(fill_status), int(bet_id)),
        )


def close_bet(
    bet_id: int,
    *,
    won: bool | None,
    pnl: float,
    reason: str,
    fill_status: str | None = None,
) -> None:
    won_val: int | None
    if won is None:
        won_val = None
    else:
        won_val = 1 if won else 0
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE predict5m_bets
            SET status = 'closed', won = ?, pnl = ?, reason = ?,
                fill_status = COALESCE(?, fill_status),
                closed_at = datetime('now')
            WHERE id = ?
            """,
            (won_val, float(pnl), reason, fill_status, int(bet_id)),
        )


def recent_bets(limit: int = 80) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM predict5m_bets ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(r) for r in rows]


def summary() -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS n,
              SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS n_open,
              SUM(CASE WHEN status = 'closed' AND won = 1 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN status = 'closed' AND won = 0 THEN 1 ELSE 0 END) AS losses,
              SUM(CASE WHEN status = 'closed' AND won IS NULL THEN 1 ELSE 0 END) AS unfilled,
              SUM(CASE WHEN status = 'closed' THEN COALESCE(pnl, 0) ELSE 0 END) AS pnl
            FROM predict5m_bets
            """
        ).fetchone()
    n = int(row["n"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    filled = wins + losses
    return {
        "n": n,
        "n_open": int(row["n_open"] or 0),
        "wins": wins,
        "losses": losses,
        "unfilled": int(row["unfilled"] or 0),
        "filled": filled,
        "pnl": float(row["pnl"] or 0),
        "winrate": (wins / filled) if filled else None,
    }

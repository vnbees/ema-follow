"""PROD-only: turn on fade5m, turn off trend4h/SMC, enable trading. Does not touch positions."""

from __future__ import annotations

import grp
import os
import pwd
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2

APP = Path("/home/bot/app")
ENV = APP / ".env"
DB = APP / "data" / "bot.db"
KEYS = {
    "FADE5M_ENABLED": "true",
    "FADE5M_SYMBOLS": (
        "SOLUSDT,XRPUSDT,DOGEUSDT,HYPEUSDT,WLDUSDT,SUIUSDT,ARBUSDT,ENAUSDT,"
        "TIAUSDT,ADAUSDT,TAOUSDT,FILUSDT,INJUSDT,DOTUSDT,APTUSDT,OPUSDT,"
        "XLMUSDT,TRXUSDT,ATOMUSDT,ZECUSDT"
    ),
    "FADE5M_SYMBOLS_NEXT": "XRPUSDT,DOGEUSDT,SUIUSDT,XLMUSDT,ATOMUSDT",
    "FADE5M_MARGIN_USDT": "1",
    "FADE5M_START_AT": "2026-10-20 23:00",
    "FADE5M_SWITCH_AT": "2026-10-21 07:00",
    "FADE5M_LIVE_NOW": "",
    "TREND4H_ENABLED": "false",
    "SMC_ENABLED": "false",
}


def _upsert_env(path: Path) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    copy2(path, path.with_name(f".env.bak-fade5m-{ts}"))
    lines = path.read_text().splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in KEYS:
            out.append(f"{key}={KEYS[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in KEYS.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")
    bot = pwd.getpwnam("bot")
    os.chown(path, bot.pw_uid, grp.getgrnam("bot").gr_gid)


def _clear_fade5m_book() -> None:
    """User already flattened on the exchange — drop ghost lots so 23:00 does not cover them."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    conn = sqlite3.connect(DB)
    try:
        pending = conn.execute(
            "UPDATE fade5m_orders SET status = 'cancelled', reason = 'user_flat', closed_ts = ? "
            "WHERE status = 'pending'",
            (now_ms,),
        ).rowcount
        opened = conn.execute(
            "UPDATE fade5m_orders SET status = 'closed', reason = 'user_flat', closed_ts = ?, "
            "exit_px = COALESCE(entry_px, limit_px), pnl = 0, fee_close = 0 "
            "WHERE status = 'open'",
            (now_ms,),
        ).rowcount
        conn.commit()
        left = conn.execute(
            "SELECT status, count(*) FROM fade5m_orders GROUP BY 1"
        ).fetchall()
        print("fade5m book cleared pending", pending, "open", opened, "now", left)
    finally:
        conn.close()


def _enable_trading() -> None:
    conn = sqlite3.connect(DB)
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('trading_enabled', 'true') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        conn.commit()
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'trading_enabled'"
        ).fetchone()
        print("trading_enabled", row[0] if row else None)
    finally:
        conn.close()


if __name__ == "__main__":
    _upsert_env(ENV)
    print("env", KEYS)
    _clear_fade5m_book()
    _enable_trading()

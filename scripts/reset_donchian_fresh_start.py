#!/usr/bin/env python3
"""Reset Donchian bot state for a fresh start (positions already flat on exchange).

- Close any open lots in DB as RESET_FRESH (no exchange orders)
- Clear donchian_state + donchian_skips
- Refresh account snapshot (0 positions if flat)

Usage:
  .venv/bin/python scripts/reset_donchian_fresh_start.py
  .venv/bin/python scripts/reset_donchian_fresh_start.py --yes
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.donchian import store  # noqa: E402
from src.database import get_connection  # noqa: E402


def reset_db() -> tuple[int, int]:
    store.ensure_schema()
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM donchian_lots WHERE status='open'")
        open_n = int(cur.fetchone()[0])
        conn.execute(
            """
            UPDATE donchian_lots
            SET status='closed', close_reason='RESET_FRESH', closed_at=?, updated_at=?
            WHERE status='open'
            """,
            (now, now),
        )
        cur = conn.execute("SELECT COUNT(*) FROM donchian_state")
        state_n = int(cur.fetchone()[0])
        conn.execute("DELETE FROM donchian_state")
        conn.execute("DELETE FROM donchian_skips")
        conn.commit()
    return open_n, state_n


def sync_account() -> None:
    from scripts.sync_prod_deploy_data import _sync_account_snapshot  # noqa: WPS433

    _sync_account_snapshot()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    parser.add_argument("--skip-account", action="store_true", help="Do not REST-sync account")
    args = parser.parse_args()

    if not args.yes:
        print("This will reset Donchian DB state (assume exchange is flat). Re-run with --yes")
        return 1

    open_n, state_n = reset_db()
    print(f"OK: closed {open_n} open lot(s) in DB, cleared {state_n} state row(s)")

    if not args.skip_account:
        try:
            sync_account()
            print("OK: account snapshot synced from REST")
        except Exception as exc:
            print(f"WARN: account sync failed: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

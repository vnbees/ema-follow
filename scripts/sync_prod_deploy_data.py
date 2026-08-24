#!/usr/bin/env python3
"""Sync data/local files trước upload PROD — chạy khi bot LOCAL đang chạy.

Ghi đè:
  - data/binance_ws_account.json (positions + balance từ REST)
  - checkpoint data/bot.db (flush WAL)

Không ghi candles — bot đang chạy tự persist qua save_candles_snapshot().
Nếu candles thiếu: để local chạy thêm, không REST seed ở đây.

Usage:
  .venv/bin/python scripts/sync_prod_deploy_data.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ACCOUNT_FILE = DATA / "binance_ws_account.json"

sys.path.insert(0, str(ROOT))

from src.donchian import store  # noqa: E402
from src.exchange.binance import (  # noqa: E402
    fetch_all_open_positions_rest,
    fetch_futures_balance_rest,
    has_credentials,
)
from src.exchange.types import Position  # noqa: E402


def _checkpoint_db() -> None:
    db = DATA / "bot.db"
    if not db.is_file():
        raise FileNotFoundError("data/bot.db missing")
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()
    print(f"OK: bot.db checkpoint ({db.stat().st_size // 1024} KB)")


def _sync_account_snapshot() -> None:
    if not has_credentials():
        raise RuntimeError("No Binance credentials")

    positions = [
        p for p in fetch_all_open_positions_rest() if abs(p.size) > 1e-12
    ]
    bal = fetch_futures_balance_rest()

    payload = {
        "positions": [
            {
                "symbol": p.symbol,
                "side": p.side,
                "size": p.size,
                "avg_price": p.avg_price,
                "unrealized_pnl": p.unrealized_pnl,
            }
            for p in positions
        ],
        "balance": {
            "margin_coin": bal.margin_coin,
            "available": bal.available,
            "account_equity": bal.account_equity,
            "usdt_equity": bal.usdt_equity,
            "total_maint_margin": bal.total_maint_margin,
            "total_initial_margin": bal.total_initial_margin,
        },
    }
    DATA.mkdir(parents=True, exist_ok=True)
    ACCOUNT_FILE.write_text(json.dumps(payload), encoding="utf-8")
    print(
        f"OK: account snapshot — positions={len(positions)} "
        f"equity={bal.account_equity:.2f} USDT"
    )


def _verify_db_vs_exchange() -> None:
    db_open = store.count_open()
    ex_open = len(
        [p for p in fetch_all_open_positions_rest() if abs(p.size) > 1e-12]
    )
    if db_open != ex_open:
        raise RuntimeError(f"DB open={db_open} != exchange={ex_open}")
    print(f"OK: DB open = exchange = {db_open}")


def main() -> int:
    try:
        _checkpoint_db()
        _sync_account_snapshot()
        _verify_db_vs_exchange()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("\nSynced. Next: preflight_prod_deploy.py --strict-candles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

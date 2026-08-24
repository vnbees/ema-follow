#!/usr/bin/env python3
"""Pre-flight gate trước deploy PROD — phải exit 0 mới được upload/deploy.

Chạy khi bot LOCAL đang chạy ổn (cache WS đầy). Sau khi pass → tắt local → upload volume.

Usage:
  .venv/bin/python scripts/preflight_prod_deploy.py
  .venv/bin/python scripts/preflight_prod_deploy.py --strict-candles   # yêu cầu 20/20 symbol ≥44 nến
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# Import sau khi chắc chắn cwd
sys.path.insert(0, str(ROOT))

from src.donchian.config import BACKTEST_SYMBOLS_20, WARMUP_MIN_BARS  # noqa: E402


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def main() -> int:
    parser = argparse.ArgumentParser(description="PROD deploy pre-flight")
    parser.add_argument(
        "--strict-candles",
        action="store_true",
        help=f"Require all {len(BACKTEST_SYMBOLS_20)} fixed symbols with ≥{WARMUP_MIN_BARS} candles",
    )
    args = parser.parse_args()
    errors: list[str] = []

    required_files = [
        DATA / "bot.db",
        DATA / "binance_exchange_info.json",
        DATA / "binance_listen_key",
        DATA / "binance_ws_account.json",
        DATA / "binance_ws_candles.json",
    ]
    for path in required_files:
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"Missing or empty: {path.name}")

    if (DATA / "binance_rate_limit_until_ms").is_file():
        errors.append(
            "Local binance_rate_limit_until_ms exists — do NOT upload to PROD volume"
        )

    # DB vs exchange
    try:
        from src.donchian import store
        from src.exchange.binance import fetch_all_open_positions_rest, has_credentials

        if not has_credentials():
            errors.append("No Binance credentials — cannot verify positions")
        else:
            db_open = store.count_open()
            ex_open = len(
                [p for p in fetch_all_open_positions_rest() if abs(p.size) > 1e-12]
            )
            if db_open != ex_open:
                errors.append(f"DB open={db_open} != exchange={ex_open}")
            else:
                _ok(f"DB open = exchange = {db_open}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Position check failed: {exc}")

    # Candle cache
    try:
        raw = json.loads((DATA / "binance_ws_candles.json").read_text())
        candles: dict = raw.get("candles") or {}
        fixed = [s.upper() for s in BACKTEST_SYMBOLS_20]
        missing = [s for s in fixed if s not in candles]
        short = [s for s in fixed if s in candles and len(candles[s]) < WARMUP_MIN_BARS]
        n_candles = len(candles)
        print(
            f"candles: total_symbols={n_candles}  "
            f"fixed_missing={len(missing)}  fixed_short(<{WARMUP_MIN_BARS})={len(short)}"
        )
        if missing:
            print(f"  missing fixed: {', '.join(missing)}")
        if short:
            print(f"  short bars: {', '.join(short)}")

        if args.strict_candles:
            if missing or short:
                errors.append(
                    f"--strict-candles: need all {len(fixed)} symbols with ≥{WARMUP_MIN_BARS} bars"
                )
        else:
            if n_candles < 18:
                errors.append(f"candles_symbols={n_candles} < 18 — run local longer")
            if len(missing) > 2:
                errors.append(f"{len(missing)} fixed symbols missing from cache")
            if len(short) > 2:
                errors.append(
                    f"{len(short)} fixed symbols have <{WARMUP_MIN_BARS} bars — boot will REST seed"
                )
        if not errors:
            _ok("candle cache acceptable for PROD boot")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Candle cache check failed: {exc}")

    # Account snapshot
    try:
        from src.exchange.binance import fetch_all_open_positions_rest

        acct = json.loads((DATA / "binance_ws_account.json").read_text())
        snap_n = len(
            [
                p
                for p in (acct.get("positions") or [])
                if float(p.get("size") or 0) > 1e-12
            ]
        )
        ex_n = len(
            [p for p in fetch_all_open_positions_rest() if abs(p.size) > 1e-12]
        )
        if snap_n != ex_n:
            errors.append(
                f"account snapshot positions={snap_n} != exchange={ex_n} — run refresh script"
            )
        else:
            _ok(f"account snapshot positions = {snap_n}")
        if not acct.get("balance"):
            errors.append("account snapshot missing balance block")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Account snapshot check failed: {exc}")

    # File sizes sanity
    candles_mb = (DATA / "binance_ws_candles.json").stat().st_size / 1024
    if candles_mb < 2:
        errors.append(
            f"binance_ws_candles.json only {candles_mb:.1f} KB — likely empty/stale"
        )

    if errors:
        print("\n=== PRE-FLIGHT FAILED ===", file=sys.stderr)
        for e in errors:
            _fail(e)
        print(
            "\nFix issues, run local ≥30–60 min, then re-run.\n"
            "See docs/DEPLOY_RATE_LIMIT_CHECKLIST.md",
            file=sys.stderr,
        )
        return 1

    print("\n=== PRE-FLIGHT PASSED ===")
    print("Next: VPS_HOST=IP ./scripts/deploy_to_vps.sh")
    print("See docs/DEPLOY_RATE_LIMIT_CHECKLIST.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

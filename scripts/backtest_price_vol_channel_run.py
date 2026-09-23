#!/usr/bin/env python3
"""Fresh backtest — Channel + Counter + Volume (1y / 3y / 5y / max≈7y).

Binance USDT-M 15m does not have 10y history (BTC futures from ~2019-09).
Long windows auto-drop coins that lack coverage (SUI/ARB/APT/OP for 5y+).

Usage:
  .venv/bin/python scripts/backtest_price_vol_channel_run.py
  .venv/bin/python scripts/backtest_price_vol_channel_run.py --windows 1825,2500
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DOCS = ROOT / "docs"

spec = importlib.util.spec_from_file_location("pvc", ROOT / "scripts/backtest_price_vol_channel.py")
pvc = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["pvc"] = pvc
spec.loader.exec_module(pvc)

Cfg = pvc.Cfg
run = pvc.run
load_pool = pvc.load_pool


CORE_CFGS = [
    Cfg("A · ch20 vol≥1.2 rr0.5 margin1% max10", period=20, min_vol=1.2, min_pot_rr=0.5, margin_pct=0.01, max_open=10),
    Cfg("B · ch20 vol≥1.0 rr0.5 margin1% max10 (no vol filter)", period=20, min_vol=1.0, min_pot_rr=0.5, margin_pct=0.01, max_open=10),
    Cfg("C · ch20 vol≥1.2 rr0.5 margin0.5% max20 (conservative)", period=20, min_vol=1.2, min_pot_rr=0.5, margin_pct=0.005, max_open=20),
    Cfg("D · ch20 vol≥1.5 rr0.6 margin1% max10 (strict vol)", period=20, min_vol=1.5, body_hi=1.0, min_pot_rr=0.6, margin_pct=0.01, max_open=10),
]


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")


def run_window(days: int) -> list[dict]:
    print(f"\n{'='*70}\nWINDOW {days}d\n{'='*70}", flush=True)
    raw, eval_start, eval_end = load_pool(days)
    print(f"Eval: {fmt_ts(eval_start)} → {fmt_ts(eval_end)}", flush=True)
    rows = []
    for c in CORE_CFGS:
        print(f"  run {c.name}...", flush=True)
        r = run(c, raw, eval_start)
        r["window_days"] = days
        r["eval_start"] = eval_start
        r["eval_end"] = eval_end
        mark = " ✅" if r["ret_pct"] > 0 else " ❌"
        print(
            f"    ret={r['ret_pct']:+.2f}% ({r['pct_day']:+.3f}%/d) PF={r['pf']:.2f} WR={r['wr']:.1f}% "
            f"n={r['n']} t/d={r['tpd']:.1f} MaxDD={r['maxdd']:.1f}% "
            f"syms={r['n_syms']} L={r['long_pnl']:+.0f} S={r['short_pnl']:+.0f}{mark}",
            flush=True,
        )
        rows.append(r)
    return rows


def write_md(all_rows: list[dict], out_path: Path) -> None:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Channel + Counter + Volume — backtest 1y / 3y / 5y / max futures",
        "",
        f"- Generated: **{now}**",
        f"- Script: `scripts/backtest_price_vol_channel_run.py`",
        "- Data: cache `data/bt_klines_15m/` (Binance USDT-M 15m, quote_volume)",
        "- Strategy: rolling channel 20 · parallel exit · counter candle · body/ATR [0.3,1.2] · pot_rr filter · TP = opposite band",
        "- Fill: entry@close · TP on band touch · no hard SL · shared wallet compound margin",
        "",
        "## Giới hạn data",
        "",
        "- Futures USDT-M **không có 10 năm** 15m (BTC từ ~2019-09 ≈ 7y).",
        "- Pool 20 coin đầy đủ chỉ overlap ~3.4y (SUI list 2023-05).",
        "- Cửa sổ ≥5y **tự drop** coin thiếu lịch sử (thường: SUI, ARB, APT, OP).",
        "",
    ]
    for days in sorted({r["window_days"] for r in all_rows}):
        subset = [r for r in all_rows if r["window_days"] == days]
        if not subset:
            continue
        es, ee = subset[0]["eval_start"], subset[0]["eval_end"]
        actual = subset[0]["days"]
        n_syms = subset[0]["n_syms"]
        syms = ",".join(subset[0].get("symbols") or [])
        lines += [
            f"## {days} ngày yêu cầu · thực tế **{actual:.0f}d** · **{n_syms} coin** ({fmt_ts(es)} → {fmt_ts(ee)})",
            "",
            f"- Symbols: `{syms}`",
            "",
            "| Config | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | Long PnL | Short PnL | Final eq |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for r in sorted(subset, key=lambda x: x["ret_pct"], reverse=True):
            eq = r["final_eq"]
            lines.append(
                f"| {r['name']} | {r['ret_pct']:+.1f}% | {r['pct_day']:+.3f}% | {r['pf']:.2f} | {r['wr']:.1f}% | "
                f"{r['n']} | {r['tpd']:.1f} | {r['maxdd']:.1f}% | {r['long_pnl']:+.0f} | {r['short_pnl']:+.0f} | {eq:.0f} |"
            )
        lines.append("")

    lines += [
        "## Ghi chú",
        "",
        "- Return % trên vốn $1000, **compound** (không rút lời) — số tuyệt đối phình trên cửa sổ dài.",
        "- Metric so sánh ổn định hơn: **%/ngày, PF, WR, MaxDD, t/d**.",
        "- Paper only.",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--windows",
        default="365,1095,1825,2100",
        help="comma days: 365=1y, 1095=3y, 1825=5y, 2100≈max multi-coin (~5.8y; futures cap)",
    )
    parser.add_argument(
        "--out",
        default="docs/backtest_channel_vol_1y_3y_5y_max.md",
        help="markdown output path",
    )
    args = parser.parse_args()
    windows = [int(x.strip()) for x in args.windows.split(",") if x.strip()]
    all_rows: list[dict] = []
    for d in windows:
        all_rows.extend(run_window(d))
    out = ROOT / args.out
    write_md(all_rows, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

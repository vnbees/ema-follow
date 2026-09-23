#!/usr/bin/env python3
"""Config A + EMA 21/89/200 filters — compare to baseline.

Usage:
  .venv/bin/python scripts/backtest_price_vol_channel_ema_filter.py
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

BASE = dict(period=20, min_vol=1.2, min_pot_rr=0.5, margin_pct=0.01, max_open=10, size_mult_cap=2.0)


def variants() -> list[Cfg]:
    return [
        Cfg("A baseline (no EMA)", ema_filter=None, **BASE),
        Cfg("A + EMA stack 21>89>200", ema_filter="stack", **BASE),
        Cfg("A + EMA soft 21 vs 89", ema_filter="stack_soft", **BASE),
        Cfg("A + price vs EMA200", ema_filter="ema200", **BASE),
    ]


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default="365,1095,1825")
    args = parser.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]

    all_rows: list[dict] = []
    for days in windows:
        print(f"\n{'='*70}\nWINDOW {days}d\n{'='*70}", flush=True)
        raw, eval_start, eval_end = load_pool(days)
        print(f"Eval: {fmt_ts(eval_start)} → {fmt_ts(eval_end)}", flush=True)
        for c in variants():
            print(f"  run {c.name}...", flush=True)
            r = run(c, raw, eval_start)
            r["window_days"] = days
            r["eval_start"] = eval_start
            r["eval_end"] = eval_end
            print(
                f"    ret={r['ret_pct']:+.1f}% PF={r['pf']:.2f} WR={r['wr']:.1f}% "
                f"n={r['n']} t/d={r['tpd']:.1f} MaxDD={r['maxdd']:.1f}%",
                flush=True,
            )
            all_rows.append(r)

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Config A + EMA 21 / 89 / 200 — backtest",
        "",
        f"- Generated: **{now}**",
        "- Script: `scripts/backtest_price_vol_channel_ema_filter.py`",
        "- Baseline = live Config A (channel + counter + vol≥1.2, TP band, size_mult=min(pot_rr,2))",
        "- **stack**: long chỉ khi EMA21>EMA89>EMA200; short khi đảo ngược",
        "- **stack_soft**: long khi EMA21>EMA89; short khi EMA21<EMA89",
        "- **ema200**: long khi close>EMA200; short khi close<EMA200",
        "",
    ]
    for days in windows:
        subset = [r for r in all_rows if r["window_days"] == days]
        if not subset:
            continue
        base = subset[0]
        es, ee = subset[0]["eval_start"], subset[0]["eval_end"]
        lines += [
            f"## {days}d · thực tế **{subset[0]['days']:.0f}d** · **{subset[0]['n_syms']} coin** ({fmt_ts(es)} → {fmt_ts(ee)})",
            "",
            "| Variant | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | ΔPF | ΔMaxDD | Final eq |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for r in subset:
            lines.append(
                f"| {r['name']} | {r['ret_pct']:+.1f}% | {r['pct_day']:+.3f}% | {r['pf']:.2f} | {r['wr']:.1f}% | "
                f"{r['n']} | {r['tpd']:.1f} | {r['maxdd']:.1f}% | "
                f"{r['pf'] - base['pf']:+.2f} | {r['maxdd'] - base['maxdd']:+.1f}pp | {r['final_eq']:.0f} |"
            )
        lines.append("")

    lines += [
        "## Kết luận",
        "",
        "- Chỉ đáng thêm EMA nếu PF giữ/↑ và MaxDD ↓ rõ, không cắt quá nhiều t/d.",
        "- Nếu PF↓ hoặc return sập mạnh → filter EMA đang chặn đúng lệnh thắng của Config A.",
        "",
    ]
    out = DOCS / "backtest_channel_vol_A_ema21_89_200.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

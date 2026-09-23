#!/usr/bin/env python3
"""Quick BT: Config A baseline vs time-stop 6/8h + size_mult_cap=1.2.

Windows: 365 / 1095 / 1825 / 2100 (all available cache coverage).

Usage:
  .venv/bin/python scripts/backtest_price_vol_channel_timestop_cap.py
"""

from __future__ import annotations

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

WINDOWS = [365, 1095, 1825, 2100]


def variants() -> list[Cfg]:
    base = dict(period=20, min_vol=1.2, min_pot_rr=0.5, margin_pct=0.01, max_open=10, size_by_rr=True)
    return [
        Cfg("A baseline (cap2, no time)", size_mult_cap=2.0, time_stop_hours=None, **base),
        Cfg("A cap1.2 only", size_mult_cap=1.2, time_stop_hours=None, **base),
        Cfg("A time6h only", size_mult_cap=2.0, time_stop_hours=6.0, **base),
        Cfg("A time8h only", size_mult_cap=2.0, time_stop_hours=8.0, **base),
        Cfg("A cap1.2 + time6h", size_mult_cap=1.2, time_stop_hours=6.0, **base),
        Cfg("A cap1.2 + time8h", size_mult_cap=1.2, time_stop_hours=8.0, **base),
    ]


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")


def main() -> int:
    all_rows: list[dict] = []
    for days in WINDOWS:
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
        "# Config A — time-stop 6/8h + size_mult_cap 1.2",
        "",
        f"- Generated: **{now}**",
        "- Script: `scripts/backtest_price_vol_channel_timestop_cap.py`",
        "- Baseline = live Config A (vol≥1.2, rr≥0.5, margin 1%, max10, size_mult=min(pot_rr,2))",
        "- TIME exit: nếu chưa chạm band TP sau N giờ → đóng @ close nến 15m",
        "- Cap: `size_mult = min(pot_rr, 1.2)` thay vì 2.0",
        "",
    ]
    for days in WINDOWS:
        subset = [r for r in all_rows if r["window_days"] == days]
        if not subset:
            continue
        es, ee = subset[0]["eval_start"], subset[0]["eval_end"]
        actual = subset[0]["days"]
        n_syms = subset[0]["n_syms"]
        base = next(r for r in subset if r["name"].startswith("A baseline"))
        lines += [
            f"## {days}d yêu cầu · thực tế **{actual:.0f}d** · **{n_syms} coin** ({fmt_ts(es)} → {fmt_ts(ee)})",
            "",
            "| Variant | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | ΔPF vs base | ΔMaxDD | Final eq |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for r in subset:
            dpf = r["pf"] - base["pf"]
            ddd = r["maxdd"] - base["maxdd"]
            lines.append(
                f"| {r['name']} | {r['ret_pct']:+.1f}% | {r['pct_day']:+.3f}% | {r['pf']:.2f} | {r['wr']:.1f}% | "
                f"{r['n']} | {r['tpd']:.1f} | {r['maxdd']:.1f}% | {dpf:+.2f} | {ddd:+.1f}pp | {r['final_eq']:.0f} |"
            )
        lines.append("")

    lines += [
        "## Đọc nhanh",
        "",
        "- Ưu tiên **PF ↑ + MaxDD ↓**; return compound tuyệt đối phình trên cửa sổ dài.",
        "- Combo đề xuất live: **cap1.2 + time6h hoặc time8h** nếu MaxDD/PF cải thiện ổn định mọi cửa sổ.",
        "",
    ]
    out = DOCS / "backtest_channel_vol_A_timestop_cap.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

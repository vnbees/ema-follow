#!/usr/bin/env python3
"""BT: time-stop chỉ khi đang lỗ (underwater), ± cap size_mult=1.2.

So với baseline Config A và time-stop cứng (đã chạy trước).

Usage:
  .venv/bin/python scripts/backtest_price_vol_channel_timestop_uw.py
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
        Cfg("A baseline", size_mult_cap=2.0, time_stop_hours=None, **base),
        Cfg("A cap1.2", size_mult_cap=1.2, time_stop_hours=None, **base),
        Cfg("A time6h UW", size_mult_cap=2.0, time_stop_hours=6.0, time_stop_underwater_only=True, **base),
        Cfg("A time8h UW", size_mult_cap=2.0, time_stop_hours=8.0, time_stop_underwater_only=True, **base),
        Cfg("A cap1.2 + time6h UW", size_mult_cap=1.2, time_stop_hours=6.0, time_stop_underwater_only=True, **base),
        Cfg("A cap1.2 + time8h UW", size_mult_cap=1.2, time_stop_hours=8.0, time_stop_underwater_only=True, **base),
        # reference: hard flatten (known bad for PF)
        Cfg("A time8h HARD", size_mult_cap=2.0, time_stop_hours=8.0, time_stop_underwater_only=False, **base),
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
        "# Config A — time-stop underwater-only (± cap 1.2)",
        "",
        f"- Generated: **{now}**",
        "- Script: `scripts/backtest_price_vol_channel_timestop_uw.py`",
        "- **UW** = sau N giờ, chỉ đóng nếu unrealized PnL @ close ≤ 0; lệnh đang lãi giữ tiếp tới TP band",
        "- **HARD** = đóng hết sau N giờ (tham chiếu)",
        "",
    ]
    for days in WINDOWS:
        subset = [r for r in all_rows if r["window_days"] == days]
        if not subset:
            continue
        es, ee = subset[0]["eval_start"], subset[0]["eval_end"]
        actual = subset[0]["days"]
        n_syms = subset[0]["n_syms"]
        base = next(r for r in subset if r["name"] == "A baseline")
        lines += [
            f"## {days}d · thực tế **{actual:.0f}d** · **{n_syms} coin** ({fmt_ts(es)} → {fmt_ts(ee)})",
            "",
            "| Variant | Return | PF | WR | Trades | t/d | MaxDD | ΔPF | ΔMaxDD | Final eq |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for r in subset:
            lines.append(
                f"| {r['name']} | {r['ret_pct']:+.1f}% | {r['pf']:.2f} | {r['wr']:.1f}% | "
                f"{r['n']} | {r['tpd']:.1f} | {r['maxdd']:.1f}% | "
                f"{r['pf'] - base['pf']:+.2f} | {r['maxdd'] - base['maxdd']:+.1f}pp | {r['final_eq']:.0f} |"
            )
        lines.append("")

    lines += [
        "## Đọc nhanh",
        "",
        "- UW tốt nếu PF gần baseline và MaxDD ↓ vs baseline/HARD.",
        "- So với `cap1.2` thuần: chọn cái PF ổn + DD thấp hơn.",
        "",
    ]
    out = DOCS / "backtest_channel_vol_A_timestop_uw.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

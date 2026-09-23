#!/usr/bin/env python3
"""Config A — monthly P/L breakdown (compound equity SoM→EoM).

Usage:
  .venv/bin/python scripts/backtest_price_vol_channel_monthly.py
"""

from __future__ import annotations

import importlib.util
import statistics as stats
import sys
from collections import defaultdict
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
CFG_A = Cfg(
    "A · ch20 vol≥1.2 rr0.5 margin1% max10",
    period=20,
    min_vol=1.2,
    min_pot_rr=0.5,
    margin_pct=0.01,
    max_open=10,
    size_mult_cap=2.0,
)


def month_key(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m")


def monthly_returns(eod: list[tuple[int, float]]) -> list[dict]:
    """% change within each calendar month (SoM first EOD → EoM last EOD)."""
    by_m: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for ts, eq in eod:
        by_m[month_key(ts)].append((ts, eq))
    rows = []
    for m in sorted(by_m):
        pts = sorted(by_m[m], key=lambda x: x[0])
        start_eq, end_eq = pts[0][1], pts[-1][1]
        if start_eq <= 0:
            continue
        ret = (end_eq / start_eq - 1.0) * 100.0
        rows.append(
            {
                "month": m,
                "start": start_eq,
                "end": end_eq,
                "ret_pct": ret,
                "days": len(pts),
            }
        )
    return rows


def streak_stats(rets: list[float]) -> dict:
    """Consecutive losing / winning month streaks."""
    max_loss = cur_loss = 0
    max_win = cur_win = 0
    loss_streaks: list[int] = []
    for r in rets:
        if r < 0:
            cur_loss += 1
            cur_win = 0
            max_loss = max(max_loss, cur_loss)
        else:
            if cur_loss > 0:
                loss_streaks.append(cur_loss)
            cur_loss = 0
            cur_win += 1
            max_win = max(max_win, cur_win)
    if cur_loss > 0:
        loss_streaks.append(cur_loss)
    return {
        "max_loss_streak": max_loss,
        "max_win_streak": max_win,
        "loss_streaks": loss_streaks,
        "n_loss_streaks": len(loss_streaks),
        "avg_loss_streak": stats.mean(loss_streaks) if loss_streaks else 0.0,
    }


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


def main() -> int:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Config A — phân tích theo tháng (baseline, không đổi size)",
        "",
        f"- Generated: **{now}**",
        "- Script: `scripts/backtest_price_vol_channel_monthly.py`",
        "- Metric: **% equity đầu tháng → cuối tháng** (compound wallet, không skim)",
        "- Config A: vol≥1.2, rr≥0.5, margin 1%, max10, `size_mult=min(pot_rr,2)`",
        "",
    ]

    for days in WINDOWS:
        print(f"\n{'='*70}\nWINDOW {days}d\n{'='*70}", flush=True)
        raw, eval_start, eval_end = load_pool(days)
        print(f"Eval: {fmt_ts(eval_start)} → {fmt_ts(eval_end)}", flush=True)
        r = run(CFG_A, raw, eval_start, record_eod=True)
        months = monthly_returns(r["eod_curve"])
        # drop partial first/last if < 20 calendar days of EOD samples (warmup edge)
        full = [m for m in months if m["days"] >= 20]
        rets = [m["ret_pct"] for m in full]
        pos = [x for x in rets if x > 0]
        neg = [x for x in rets if x <= 0]
        st = streak_stats(rets)

        print(
            f"  months={len(full)} pos={len(pos)} neg={len(neg)} "
            f"avg={stats.mean(rets):+.2f}% med={stats.median(rets):+.2f}% "
            f"max_loss_streak={st['max_loss_streak']}",
            flush=True,
        )

        lines += [
            f"## {days}d · thực tế **{r['days']:.0f}d** · **{r['n_syms']} coin** "
            f"({fmt_ts(eval_start)} → {fmt_ts(eval_end)})",
            "",
            f"- Overall: PF={r['pf']:.2f} WR={r['wr']:.1f}% MaxDD={r['maxdd']:.1f}% t/d={r['tpd']:.1f}",
            f"- Tháng đủ (≥20 EOD): **{len(full)}** · lãi **{len(pos)}** · lỗ/hòa **{len(neg)}** "
            f"(**{100 * len(pos) / len(full):.0f}%** tháng dương)",
            f"- **TB tháng: {stats.mean(rets):+.2f}%** · trung vị **{stats.median(rets):+.2f}%**",
            f"- Best: **{max(rets):+.1f}%** · Worst: **{min(rets):+.1f}%**",
            f"- Chuỗi lỗ liên tiếp max: **{st['max_loss_streak']}** tháng"
            + (f" · TB độ dài streak lỗ: {st['avg_loss_streak']:.1f}" if st["n_loss_streaks"] else ""),
            f"- Chuỗi lãi liên tiếp max: **{st['max_win_streak']}** tháng",
            "",
        ]
        if neg:
            lines += [
                "### Tháng lỗ",
                "",
                "| Tháng | Return | Equity SoM → EoM |",
                "| --- | ---: | ---: |",
            ]
            for m in full:
                if m["ret_pct"] <= 0:
                    lines.append(
                        f"| {m['month']} | **{m['ret_pct']:+.2f}%** | {m['start']:.0f} → {m['end']:.0f} |"
                    )
            lines.append("")
            # consecutive loss episodes
            lines += ["### Chuỗi lỗ liên tiếp", "", "| Từ → Đến | Số tháng | Tổng % (cộng đơn) |", "| --- | ---: | ---: |"]
            i = 0
            while i < len(full):
                if full[i]["ret_pct"] > 0:
                    i += 1
                    continue
                j = i
                while j < len(full) and full[j]["ret_pct"] <= 0:
                    j += 1
                chunk = full[i:j]
                lines.append(
                    f"| {chunk[0]['month']} → {chunk[-1]['month']} | {len(chunk)} | "
                    f"{sum(c['ret_pct'] for c in chunk):+.1f}% |"
                )
                i = j
            lines.append("")
        else:
            lines += ["- **Không có tháng lỗ** trong cửa sổ này.", ""]

        lines += [
            "<details><summary>Toàn bộ tháng</summary>",
            "",
            "| Tháng | Return | SoM | EoM | ngày EOD |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for m in full:
            mark = " ❌" if m["ret_pct"] <= 0 else ""
            lines.append(
                f"| {m['month']} | {m['ret_pct']:+.2f}%{mark} | {m['start']:.0f} | {m['end']:.0f} | {m['days']} |"
            )
        lines += ["", "</details>", ""]

    lines += [
        "## Kết luận ngắn",
        "",
        "- **Không** phải tháng nào cũng lãi — xem tỷ lệ tháng dương và chuỗi lỗ max ở từng cửa sổ.",
        "- %/tháng compound phình khi equity lớn; nhìn **tỷ lệ tháng dương + worst month + loss streak** ổn định hơn return tuyệt đối.",
        "- Paper, không skim, không slippage thực tế.",
        "",
    ]
    out = DOCS / "backtest_channel_vol_A_monthly.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

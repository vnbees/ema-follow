#!/usr/bin/env python3
"""SMC Range + 3TF filter — chỉ long khi NO_TREND/TREND_UP, chỉ short khi NO_TREND/TREND_DOWN."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# Load tp2 (có fetch_labeled_frames, slice_m5, label_tf_fast)
spec = importlib.util.spec_from_file_location("tp2", ROOT / "scripts" / "backtest_link_tp2_3tf.py")
tp2 = importlib.util.module_from_spec(spec)
sys.modules["tp2"] = tp2
spec.loader.exec_module(tp2)

# Load smc (có compute_signals, run)
spec2 = importlib.util.spec_from_file_location("smc", ROOT / "scripts" / "backtest_link_smc.py")
smc_mod = importlib.util.module_from_spec(spec2)
sys.modules["smc"] = smc_mod
spec2.loader.exec_module(smc_mod)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
LOOKBACK_DAYS = 365
RANGE_PERIOD = 50
MAX_RANGE_PCT = 0.02

# ── 3TF filter modes ──────────────────────────────────────────────────────────
# "strict"  : long chỉ khi TREND_UP, short chỉ khi TREND_DOWN
# "notrend" : long+short chỉ khi NO_TREND
# "aligned" : long khi TREND_UP|NO_TREND, short khi TREND_DOWN|NO_TREND
FILTER_MODES = ("strict", "notrend", "aligned")
FILTER_LABELS = {
    "strict":   "Strict (L: TREND_UP only, S: TREND_DOWN only)",
    "notrend":  "NO_TREND only (L+S chỉ khi sideway)",
    "aligned":  "Aligned (L: UP|NO_TREND, S: DOWN|NO_TREND)",
}
WINDOWS = (30, 90, 365)


def apply_3tf_filter(df: pd.DataFrame, filter_mode: str) -> pd.DataFrame:
    """Lọc signal SMC dựa trên 3TF aligned label."""
    df = df.copy()
    sig = df["signal"].copy()
    aligned = df["aligned"]

    if filter_mode == "strict":
        # Long chỉ khi TREND_UP, short chỉ khi TREND_DOWN
        sig = np.where((sig == 1) & (aligned == "TREND_UP"), 1,
               np.where((sig == -1) & (aligned == "TREND_DOWN"), -1, 0))
    elif filter_mode == "notrend":
        # Cả hai chiều chỉ khi NO_TREND
        sig = np.where((sig != 0) & (aligned == "NO_TREND"), sig, 0)
    elif filter_mode == "aligned":
        # Long: TREND_UP hoặc NO_TREND; Short: TREND_DOWN hoặc NO_TREND
        sig = np.where(
            (sig == 1) & (aligned.isin(["TREND_UP", "NO_TREND"])), 1,
            np.where(
                (sig == -1) & (aligned.isin(["TREND_DOWN", "NO_TREND"])), -1, 0
            )
        )

    df["signal"] = sig
    # Cập nhật lại SL / TP theo signal đã lọc
    df["sl"] = np.where(
        df["signal"] == 1, df["low"] * 0.998,
        np.where(df["signal"] == -1, df["high"] * 1.002, np.nan)
    )
    df["tp_far"] = np.where(
        df["signal"] == 1, df["box_high"],
        np.where(df["signal"] == -1, df["box_low"], np.nan)
    )
    df["tp_mid"] = df["box_mid"]
    return df


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    print("Fetching 365d klines (5m + 1h + 4h)...", flush=True)
    frames = tp2.fetch_labeled_frames(LOOKBACK_DAYS)

    # Prep 5m với 3TF aligned
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_ms = tp2.mtf.TF["5m"]
    last_closed = (now_ms // bar_ms) * bar_ms

    # Tính SMC signals trên toàn bộ raw 5m (cần warmup RANGE_PERIOD nến)
    raw5 = frames["5m"].copy()
    raw5_smc = smc_mod.compute_signals(raw5, RANGE_PERIOD, MAX_RANGE_PCT)

    summary_lines = [
        "# SMC + 3TF Filter — LINK 5m · 30 / 90 / 365 ngày",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- **SMC base**: sweep_low → long, sweep_high → short · box 50 nến · range ≤ 2%",
        "- **3TF filter**: 5m + 1h + 4h aligned label (`TREND_UP / TREND_DOWN / NO_TREND`)",
        "- SL 0.2% ngoài râu · TP = biên đối diện · Risk 1%/lệnh · 10x · phí 0.04%/side",
        "- So sánh thêm: SMC thuần (không filter) từ backtest trước",
        "",
        "## Tổng hợp",
        "",
        "| Cửa sổ | Filter | Signals | Trades | WR | PnL | % | Max DD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    table_rows: list[str] = []

    detail_blocks: list[str] = []

    for days in WINDOWS:
        window_from = last_closed - days * 24 * 3600 * 1000

        # Slice 5m và gắn HTF
        m5 = raw5_smc[raw5_smc["ts"] >= window_from].copy()
        m5 = tp2.mtf.attach_htf(m5, frames["1h"], "h1", tp2.mtf.TF["1h"])
        m5 = tp2.mtf.attach_htf(m5, frames["4h"], "h4", tp2.mtf.TF["4h"])
        m5 = m5.dropna(subset=["h1_dir", "h4_dir"]).copy()

        # Tính aligned
        up = (m5["dir"] == "UP") & (m5["h1_dir"] == "UP") & (m5["h4_dir"] == "UP")
        down = (m5["dir"] == "DOWN") & (m5["h1_dir"] == "DOWN") & (m5["h4_dir"] == "DOWN")
        m5["aligned"] = np.select([up, down], ["TREND_UP", "TREND_DOWN"], default="NO_TREND")
        m5 = m5.reset_index(drop=True)

        first_bar, last_bar = m5.iloc[0], m5.iloc[-1]
        px_chg = (last_bar.close / first_bar.close - 1) * 100
        n_up = int((m5["aligned"] == "TREND_UP").sum())
        n_dn = int((m5["aligned"] == "TREND_DOWN").sum())
        n_nt = int((m5["aligned"] == "NO_TREND").sum())

        # SMC thuần (không filter) — lấy từ backtest cũ nếu có, tính lại cho nhanh
        _, st_base = smc_mod.run(m5, tp_mode="far")

        print(f"\n=== {days}d | LINK {px_chg:+.1f}% ===", flush=True)
        print(f"  3TF: UP={n_up} DOWN={n_dn} NO_TREND={n_nt}", flush=True)
        print(f"  SMC no-filter: {st_base['n_total']} trades WR={st_base['win_rate']:.0f}% PnL={st_base['pnl']:+.1f} DD={st_base['max_dd']:+.0f}", flush=True)

        table_rows.append(
            f"| {days}d | **No filter** | {int((m5['signal']!=0).sum())} | {st_base['n_total']} | "
            f"{st_base['win_rate']:.0f}% | {st_base['pnl']:+.1f} | {st_base['pnl_pct']:+.1f}% | {st_base['max_dd']:+.0f} |"
        )

        for fmode in FILTER_MODES:
            m5f = apply_3tf_filter(m5, fmode)
            n_sig = int((m5f["signal"] != 0).sum())
            _, st = smc_mod.run(m5f, tp_mode="far")
            print(
                f"  {fmode:10s}: sig={n_sig:4d} trades={st['n_total']:4d} "
                f"WR={st['win_rate']:3.0f}% PnL={st['pnl']:+7.1f} ({st['pnl_pct']:+.1f}%) "
                f"DD={st['max_dd']:+.0f}",
                flush=True
            )
            table_rows.append(
                f"| {days}d | {FILTER_LABELS[fmode]} | {n_sig} | {st['n_total']} | "
                f"{st['win_rate']:.0f}% | **{st['pnl']:+.1f}** | **{st['pnl_pct']:+.1f}%** | {st['max_dd']:+.0f} |"
            )

        detail_blocks += [
            f"## {days} ngày",
            "",
            f"- Cửa sổ: {_local(int(first_bar.ts))} → {_local(int(last_bar.ts))}",
            f"- Giá: {first_bar.close:.4f} → {last_bar.close:.4f} ({px_chg:+.2f}%)",
            f"- 3TF TREND_UP / TREND_DOWN / NO_TREND: {n_up} / {n_dn} / {n_nt}",
            "",
        ]

    summary_lines += table_rows
    summary_lines += [""]
    summary_lines += detail_blocks

    out = ROOT / "docs" / "backtest_LINK_smc_3tf_90_365.md"
    out.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()

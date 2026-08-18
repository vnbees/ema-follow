#!/usr/bin/env python3
"""Thong ke gia quay lai muc luc RSI cham moc tren LINK 5m."""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SYMBOL = "LINKUSDT"
LOOKBACK_DAYS = 365
RSI_PERIOD = 14
MID_LOW = 48.0
MID_HIGH = 52.0
PRICE_ZONE_PCT = 0.0025
MOVE_AWAY_PCTS = (0.0025, 0.0050, 0.0100)

spec = importlib.util.spec_from_file_location("smc_fetch", ROOT / "scripts" / "backtest_link_smc.py")
smc_fetch = importlib.util.module_from_spec(spec)
sys.modules["smc_fetch"] = smc_fetch
spec.loader.exec_module(smc_fetch)


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def mark_events(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    prev = df["rsi"].shift(1)
    df["evt_rsi_ge70"] = (df["rsi"] >= 70) & (prev < 70)
    df["evt_rsi_le30"] = (df["rsi"] <= 30) & (prev > 30)
    in_mid = (df["rsi"] >= MID_LOW) & (df["rsi"] <= MID_HIGH)
    prev_in_mid = in_mid.shift(1).fillna(False)
    df["evt_rsi_50_zone"] = in_mid & (~prev_in_mid)
    return df


def analyze_revisit(
    df: pd.DataFrame, event_col: str, label: str, move_away_pct: float
) -> tuple[dict, list[dict]]:
    horizon_hours = [1, 4, 12, 24, 72, 168, 720]
    horizon_bars = {h: int(h * 60 / 5) for h in horizon_hours}
    rows: list[dict] = []
    event_idx = np.flatnonzero(df[event_col].to_numpy())

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    rsis = df["rsi"].to_numpy()
    tss = df["ts"].to_numpy()

    for idx in event_idx:
        ref_price = float(closes[idx])
        zone_low = ref_price * (1 - PRICE_ZONE_PCT)
        zone_high = ref_price * (1 + PRICE_ZONE_PCT)
        away_low = ref_price * (1 - move_away_pct)
        away_high = ref_price * (1 + move_away_pct)
        moved_away = False
        revisit_idx = None
        j = idx + 1
        while j < len(df):
            if not moved_away:
                if lows[j] <= away_low or highs[j] >= away_high:
                    moved_away = True
            else:
                if lows[j] <= zone_high and highs[j] >= zone_low:
                    revisit_idx = j
                    break
            j += 1

        row = {
            "label": label,
            "entry_idx": int(idx),
            "entry_ts": int(tss[idx]),
            "entry_price": ref_price,
            "entry_rsi": float(rsis[idx]),
            "moved_away": moved_away,
            "revisit_idx": int(revisit_idx) if revisit_idx is not None else None,
            "revisit_ts": int(tss[revisit_idx]) if revisit_idx is not None else None,
            "hours_to_revisit": ((revisit_idx - idx) * 5 / 60) if revisit_idx is not None else None,
        }
        for h in horizon_hours:
            hb = horizon_bars[h]
            end = min(len(df), idx + hb + 1)
            hit = False
            if idx + 1 < end:
                away = False
                for k in range(idx + 1, end):
                    if not away:
                        if lows[k] <= away_low or highs[k] >= away_high:
                            away = True
                    elif lows[k] <= zone_high and highs[k] >= zone_low:
                        hit = True
                        break
            row[f"hit_{h}h"] = hit
        rows.append(row)

    n = len(rows)
    eligible = [r for r in rows if r["moved_away"]]
    revisited = [r for r in eligible if r["revisit_idx"] is not None]
    hours = [r["hours_to_revisit"] for r in revisited if r["hours_to_revisit"] is not None]
    stats = {
        "label": label,
        "move_away_pct": move_away_pct,
        "n_events": n,
        "n_eligible": len(eligible),
        "n_revisited": len(revisited),
        "revisit_pct": (len(revisited) / len(eligible) * 100) if eligible else 0.0,
        "median_h": float(np.median(hours)) if hours else math.nan,
        "mean_h": float(np.mean(hours)) if hours else math.nan,
    }
    for h in horizon_hours:
        key = f"hit_{h}h"
        stats[key] = (sum(1 for r in eligible if r[key]) / len(eligible) * 100) if eligible else 0.0
    return stats, rows


def fmt_h(hours: float) -> str:
    if math.isnan(hours):
        return "-"
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def write_report(df: pd.DataFrame, summaries: list[dict], detail_map: dict[str, list[dict]]) -> Path:
    first, last = df.iloc[0], df.iloc[-1]
    px_chg = (last.close / first.close - 1) * 100
    path = ROOT / "docs" / "backtest_LINK_rsi_price_revisit_365d.md"
    lines = [
        "# LINK 5m - Gia quay lai muc luc RSI cham moc",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Symbol: `{SYMBOL}` · Khung `5m` · Cua so **{LOOKBACK_DAYS} ngay**",
        f"- Gia: {first.close:.4f} -> {last.close:.4f} ({px_chg:+.2f}%)",
        f"- RSI({RSI_PERIOD}) events:",
        f"  - `RSI >= 70`: luc RSI cross vao vung overbought",
        f"  - `RSI <= 30`: luc RSI cross vao vung oversold",
        f"  - `RSI 50 zone`: luc RSI di vao vung {MID_LOW:.0f}-{MID_HIGH:.0f}",
        f"- `Vung gia`: close luc su kien +/- {PRICE_ZONE_PCT*100:.2f}%.",
        "- `Gia quay lai`: sau khi gia da di xa toi thieu theo muc `move away`, mot nen sau do quay lai overlap vung gia.",
        "",
        "## Tong hop",
        "",
        "| Moc RSI | Move away | So lan cham | So lan di xa | % quay lai eventually | Median quay lai | Mean quay lai | 1h | 4h | 12h | 24h | 3d | 7d | 30d |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        lines.append(
            f"| {s['label']} | {s['move_away_pct']*100:.2f}% | {s['n_events']} | {s['n_eligible']} | {s['revisit_pct']:.1f}% | {fmt_h(s['median_h'])} | {fmt_h(s['mean_h'])} | "
            f"{s['hit_1h']:.1f}% | {s['hit_4h']:.1f}% | {s['hit_12h']:.1f}% | {s['hit_24h']:.1f}% | "
            f"{s['hit_72h']:.1f}% | {s['hit_168h']:.1f}% | {s['hit_720h']:.1f}% |"
        )

    for label, rows in detail_map.items():
        lines += [
            "",
            f"## Mau giao dich - {label}",
            "",
            "| # | Thoi diem | Gia luc cham | RSI | Quay lai sau | Thoi diem quay lai |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        sample = rows[:20]
        for i, r in enumerate(sample, 1):
            when = _local(r["entry_ts"])
            rev = "-" if r["hours_to_revisit"] is None else fmt_h(r["hours_to_revisit"])
            rev_ts = "-" if r["revisit_ts"] is None else _local(r["revisit_ts"])
            lines.append(
                f"| {i} | {when} | {r['entry_price']:.4f} | {r['entry_rsi']:.1f} | {rev} | {rev_ts} |"
            )
        if len(rows) > len(sample):
            lines += ["", f"({len(rows) - len(sample)} su kien khac khong hien thi)", ""]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_ms = 5 * 60 * 1000
    last_closed = (now_ms // bar_ms) * bar_ms
    warmup_ms = (RSI_PERIOD + 5) * bar_ms
    fetch_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000 - warmup_ms

    print(f"Fetching {SYMBOL} 5m from {_local(fetch_from)}...", flush=True)
    df = smc_fetch.fetch_klines("5m", fetch_from, last_closed)
    df["rsi"] = compute_rsi(df["close"], RSI_PERIOD)
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000
    df = df[df["ts"] >= window_from].copy().reset_index(drop=True)
    df = mark_events(df)

    event_defs = [
        ("evt_rsi_ge70", "RSI >= 70"),
        ("evt_rsi_le30", "RSI <= 30"),
        ("evt_rsi_50_zone", f"RSI {MID_LOW:.0f}-{MID_HIGH:.0f}"),
    ]
    summaries = []
    detail_map: dict[str, list[dict]] = {}
    for move_away_pct in MOVE_AWAY_PCTS:
        print(f"\nMove away >= {move_away_pct*100:.2f}%:", flush=True)
        for col, label in event_defs:
            stats, rows = analyze_revisit(df, col, label, move_away_pct)
            summaries.append(stats)
            detail_map[f"{label} | away {move_away_pct*100:.2f}%"] = rows
            print(
                f"{label}: n={stats['n_events']} away={stats['n_eligible']} revisit={stats['revisit_pct']:.1f}% "
                f"median={fmt_h(stats['median_h'])} 24h={stats['hit_24h']:.1f}% 7d={stats['hit_168h']:.1f}%",
                flush=True,
            )

    out = write_report(df, summaries, detail_map)
    print(f"Wrote {out}", flush=True)


if __name__ == "__main__":
    main()

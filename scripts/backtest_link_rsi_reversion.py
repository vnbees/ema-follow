#!/usr/bin/env python3
"""Backtest strategy mean reversion ve vung gia luc RSI cham moc."""

from __future__ import annotations

import importlib.util
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
ZONE_PCT = 0.0025
MOVE_AWAY_PCT = 0.0050
MARGIN_PCT = 0.01
LEVERAGE = 10.0
FEE = 0.0004
TIMEOUT_HOURS = (1, 4, 8, 12, 24, 48)

spec = importlib.util.spec_from_file_location("rsi_rev", ROOT / "scripts" / "backtest_link_rsi_price_revisit.py")
rsi_rev = importlib.util.module_from_spec(spec)
sys.modules["rsi_rev"] = rsi_rev
spec.loader.exec_module(rsi_rev)


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def mark_events(df: pd.DataFrame) -> pd.DataFrame:
    return rsi_rev.mark_events(df)


def build_event_flag(df: pd.DataFrame, mode: str) -> pd.Series:
    if mode == "rsi70":
        return df["evt_rsi_ge70"]
    if mode == "rsi30":
        return df["evt_rsi_le30"]
    if mode == "rsi50":
        return df["evt_rsi_50_zone"]
    if mode == "combined":
        return df["evt_rsi_ge70"] | df["evt_rsi_le30"] | df["evt_rsi_50_zone"]
    raise ValueError(mode)


def run_strategy(df: pd.DataFrame, event_mode: str, timeout_h: int) -> tuple[list[dict], dict]:
    timeout_bars = int(timeout_h * 60 / 5)
    event_flag = build_event_flag(df, event_mode).to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    rsis = df["rsi"].to_numpy()
    tss = df["ts"].to_numpy()

    trades: list[dict] = []
    pending_anchor: dict | None = None
    open_trade: dict | None = None
    equity = 1000.0
    equity_curve = [equity]

    for i in range(len(df)):
        px = float(closes[i])
        hi = float(highs[i])
        lo = float(lows[i])
        ts = int(tss[i])

        if open_trade is not None:
            hit_tp = False
            if open_trade["side"] == "long":
                hit_tp = hi >= open_trade["tp"]
            else:
                hit_tp = lo <= open_trade["tp"]

            if hit_tp:
                exit_px = open_trade["tp"]
                reason = "TP"
            elif i - open_trade["entry_idx"] >= timeout_bars:
                exit_px = px
                reason = "TIMEOUT"
            else:
                equity_curve.append(equity)
                continue

            if open_trade["side"] == "long":
                gross = (exit_px - open_trade["entry"]) * open_trade["qty"]
            else:
                gross = (open_trade["entry"] - exit_px) * open_trade["qty"]
            fee = (open_trade["entry"] + exit_px) * open_trade["qty"] * FEE
            pnl = gross - fee
            equity += pnl
            trades.append(
                {
                    **open_trade,
                    "exit_ts": ts,
                    "exit_px": exit_px,
                    "reason": reason,
                    "pnl": pnl,
                    "equity_after": equity,
                }
            )
            open_trade = None
            pending_anchor = None
            equity_curve.append(equity)
            continue

        if event_flag[i]:
            pending_anchor = {
                "anchor_ts": ts,
                "anchor_idx": i,
                "anchor_price": px,
                "anchor_rsi": float(rsis[i]),
            }

        if pending_anchor is not None and i > pending_anchor["anchor_idx"]:
            anchor = pending_anchor["anchor_price"]
            up_trigger = anchor * (1 + MOVE_AWAY_PCT)
            dn_trigger = anchor * (1 - MOVE_AWAY_PCT)
            side = None
            entry = None
            tp = None
            if hi >= up_trigger:
                side = "short"
                entry = px
                tp = anchor * (1 + ZONE_PCT)
            elif lo <= dn_trigger:
                side = "long"
                entry = px
                tp = anchor * (1 - ZONE_PCT)

            if side is not None:
                margin = equity * MARGIN_PCT
                notional = margin * LEVERAGE
                qty = notional / entry
                open_trade = {
                    "id": len(trades) + 1,
                    "event_mode": event_mode,
                    "timeout_h": timeout_h,
                    "side": side,
                    "anchor_ts": pending_anchor["anchor_ts"],
                    "anchor_price": anchor,
                    "anchor_rsi": pending_anchor["anchor_rsi"],
                    "entry_idx": i,
                    "entry_ts": ts,
                    "entry": entry,
                    "tp": tp,
                    "qty": qty,
                    "notional": notional,
                }

        equity_curve.append(equity)

    if open_trade is not None:
        exit_px = float(closes[-1])
        if open_trade["side"] == "long":
            gross = (exit_px - open_trade["entry"]) * open_trade["qty"]
        else:
            gross = (open_trade["entry"] - exit_px) * open_trade["qty"]
        fee = (open_trade["entry"] + exit_px) * open_trade["qty"] * FEE
        pnl = gross - fee
        equity += pnl
        trades.append(
            {
                **open_trade,
                "exit_ts": int(tss[-1]),
                "exit_px": exit_px,
                "reason": "EOD_OPEN",
                "pnl": pnl,
                "equity_after": equity,
            }
        )

    peak = equity_curve[0]
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = min(max_dd, e - peak)

    wins = [t for t in trades if t["pnl"] > 0]
    tp_trades = [t for t in trades if t["reason"] == "TP"]
    timeout_trades = [t for t in trades if t["reason"] == "TIMEOUT"]
    stats = {
        "event_mode": event_mode,
        "timeout_h": timeout_h,
        "n": len(trades),
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "tp_count": len(tp_trades),
        "timeout_count": len(timeout_trades),
        "pnl": sum(t["pnl"] for t in trades),
        "pnl_pct": (equity - 1000.0) / 1000.0 * 100,
        "avg_pnl": (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0,
        "avg_timeout_pnl": (sum(t["pnl"] for t in timeout_trades) / len(timeout_trades)) if timeout_trades else 0.0,
        "worst_timeout": (min(t["pnl"] for t in timeout_trades)) if timeout_trades else 0.0,
        "max_dd": max_dd,
    }
    return trades, stats


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_ms = 5 * 60 * 1000
    last_closed = (now_ms // bar_ms) * bar_ms
    warmup_ms = (RSI_PERIOD + 5) * bar_ms
    fetch_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000 - warmup_ms

    print(f"Fetching {SYMBOL} 5m from {_local(fetch_from)}...", flush=True)
    df = rsi_rev.smc_fetch.fetch_klines("5m", fetch_from, last_closed)
    df["rsi"] = rsi_rev.compute_rsi(df["close"], RSI_PERIOD)
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000
    df = df[df["ts"] >= window_from].copy().reset_index(drop=True)
    df = mark_events(df)

    modes = ["rsi70", "rsi30", "rsi50", "combined"]
    labels = {
        "rsi70": "Anchor RSI >= 70",
        "rsi30": "Anchor RSI <= 30",
        "rsi50": f"Anchor RSI {MID_LOW:.0f}-{MID_HIGH:.0f}",
        "combined": "Anchor any RSI zone",
    }

    first, last = df.iloc[0], df.iloc[-1]
    px_chg = (last.close / first.close - 1) * 100
    lines = [
        "# Mean Reversion ve vung gia luc RSI cham moc - LINK 5m",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cua so: **{LOOKBACK_DAYS} ngay** · Gia {first.close:.4f} -> {last.close:.4f} ({px_chg:+.2f}%)",
        f"- Event anchor: RSI >= 70, RSI <= 30, RSI {MID_LOW:.0f}-{MID_HIGH:.0f}",
        f"- Entry: gia roi xa `{MOVE_AWAY_PCT*100:.2f}%` khoi anchor -> vao lenh keo ve anchor",
        f"- TP: vao lai vung gia anchor `{ZONE_PCT*100:.2f}%`",
        f"- Size: margin {MARGIN_PCT*100:.1f}% equity / lenh · {LEVERAGE:.0f}x · phi {FEE*100:.2f}%/side",
        "- Khong dung SL cung; neu khong ve target trong timeout thi dong theo gia close.",
        "",
        "## Tong hop",
        "",
        "| Strategy | Timeout | Trades | WR | TP | Timeout | PnL | % | Avg/trade | Avg timeout | Worst timeout | Max DD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    best_by_mode: dict[str, tuple[dict, list[dict]]] = {}
    for mode in modes:
        best = None
        best_trades = None
        for timeout_h in TIMEOUT_HOURS:
            trades, stats = run_strategy(df, mode, timeout_h)
            lines.append(
                f"| {labels[mode]} | {timeout_h}h | {stats['n']} | {stats['wr']:.0f}% | "
                f"{stats['tp_count']} | {stats['timeout_count']} | {stats['pnl']:+.2f} | {stats['pnl_pct']:+.2f}% | "
                f"{stats['avg_pnl']:+.3f} | {stats['avg_timeout_pnl']:+.3f} | {stats['worst_timeout']:+.3f} | {stats['max_dd']:+.2f} |"
            )
            if best is None or stats["pnl"] > best["pnl"]:
                best = stats
                best_trades = trades
        best_by_mode[mode] = (best, best_trades)

    lines += ["", "## Ket luan nhanh", ""]
    for mode in modes:
        best, trades = best_by_mode[mode]
        lines.append(
            f"- {labels[mode]}: timeout tot nhat = **{best['timeout_h']}h**, "
            f"PnL **{best['pnl']:+.2f} USDT ({best['pnl_pct']:+.2f}%)**, "
            f"WR {best['wr']:.0f}%, timeout loss avg {best['avg_timeout_pnl']:+.3f}, max DD {best['max_dd']:+.2f}."
        )

    for mode in modes:
        best, trades = best_by_mode[mode]
        lines += [
            "",
            f"## Mau lenh - {labels[mode]} (best timeout {best['timeout_h']}h)",
            "",
            "| # | Side | Anchor time | Anchor price | Entry time | Entry | Exit time | Exit | PnL | Reason |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for t in trades[:20]:
            lines.append(
                f"| {t['id']} | {t['side']} | {_local(t['anchor_ts'])} | {t['anchor_price']:.4f} | "
                f"{_local(t['entry_ts'])} | {t['entry']:.4f} | {_local(t['exit_ts'])} | {t['exit_px']:.4f} | "
                f"{t['pnl']:+.3f} | {t['reason']} |"
            )
        if len(trades) > 20:
            lines += ["", f"({len(trades) - 20} lenh khac khong hien thi)", ""]

    out = ROOT / "docs" / "backtest_LINK_rsi_reversion_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}", flush=True)

    for mode in modes:
        best, _ = best_by_mode[mode]
        print(
            f"{labels[mode]} best={best['timeout_h']}h pnl={best['pnl']:+.2f} "
            f"wr={best['wr']:.0f}% dd={best['max_dd']:+.2f} timeout_avg={best['avg_timeout_pnl']:+.3f}",
            flush=True,
        )


if __name__ == "__main__":
    main()

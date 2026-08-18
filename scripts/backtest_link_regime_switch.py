#!/usr/bin/env python3
"""Regime switch 3TF + cap lot + vốn 1k/10x/0.5%. So sánh regime / long-only / short-only."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tp2", ROOT / "scripts" / "backtest_link_tp2_3tf.py")
tp2 = importlib.util.module_from_spec(spec)
sys.modules["tp2"] = tp2
spec.loader.exec_module(tp2)

FEE = 0.0004
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CAPITAL = 1000.0
LEVERAGE = 10.0
SIZE_PCT = 0.005
MAX_LOT_CAPS = (5, 10)
WINDOWS = (90, 365)
MODES = ("regime", "long_only", "short_only")
MODE_LABELS = {
    "regime": "Regime switch (1 chiều theo 3TF)",
    "long_only": "Long-only (TREND_UP → exit TREND_DOWN)",
    "short_only": "Short-only (TREND_DOWN → exit TREND_UP)",
}


@dataclass
class Round:
    side: str
    n_lots: int
    avg: float
    exit_px: float
    pnl: float
    fee: float
    reason: str


def _avg_entry(lots: list[tp2.Lot]) -> float:
    if not lots:
        return 0.0
    n = sum(l.entry * l.qty for l in lots)
    q = sum(l.qty for l in lots)
    return n / q if q else 0.0


def _margin(lot: tp2.Lot, lev: float) -> float:
    return lot.entry * lot.qty / lev


def _mtm_lots(lots: list[tp2.Lot], px: float) -> float:
    total = 0.0
    for lot in lots:
        if lot.side == "short":
            gross = (lot.entry - px) * lot.qty
        else:
            gross = (px - lot.entry) * lot.qty
        total += gross - (lot.entry + px) * lot.qty * FEE
    return total


def run(
    m5: pd.DataFrame,
    *,
    mode: str,
    max_lots: int,
    capital: float = CAPITAL,
    leverage: float = LEVERAGE,
    size_pct: float = SIZE_PCT,
    mmr: float = 0.004,
) -> tuple[list[tp2.Fill], dict]:
    """mode: regime | long_only | short_only."""
    longs: list[tp2.Lot] = []
    shorts: list[tp2.Lot] = []
    fills: list[tp2.Fill] = []
    rounds: list[Round] = []
    nid = 1
    cash = float(capital)
    halted = False
    lev = float(leverage)
    stats = {
        "mode": mode,
        "max_lots_cap": max_lots,
        "capital": capital,
        "long_adds": 0,
        "short_adds": 0,
        "long_skips": 0,
        "short_skips": 0,
        "cap_skips": 0,
        "max_long": 0,
        "max_short": 0,
        "max_notional": 0.0,
        "max_margin": 0.0,
        "max_dd": 0.0,
        "min_equity": capital,
        "max_equity": capital,
        "min_mtm": 0.0,
        "liquidated": False,
        "end_equity": capital,
        "n_rounds": 0,
        "round_wins": 0,
    }
    peak_eq = capital
    open_round_side: str | None = None
    open_round_lots = 0

    def _locked() -> float:
        return sum(_margin(l, lev) for l in longs + shorts)

    def _tot_notional() -> float:
        return sum(l.entry * l.qty for l in longs + shorts)

    def _equity(px: float) -> tuple[float, float]:
        mtm = _mtm_lots(longs, px) + _mtm_lots(shorts, px)
        return cash + _locked() + mtm, mtm

    def _mark(px: float) -> None:
        nonlocal peak_eq
        eq, mtm = _equity(px)
        peak_eq = max(peak_eq, eq)
        stats["max_dd"] = min(stats["max_dd"], eq - peak_eq)
        stats["min_equity"] = min(stats["min_equity"], eq)
        stats["max_equity"] = max(stats["max_equity"], eq)
        stats["min_mtm"] = min(stats["min_mtm"], mtm)
        stats["max_notional"] = max(stats["max_notional"], _tot_notional())
        stats["max_margin"] = max(stats["max_margin"], _locked())

    def _close_side(book: list[tp2.Lot], px: float, ts: int, reason: str) -> list[tp2.Lot]:
        nonlocal cash, open_round_side, open_round_lots
        if not book:
            return book
        side = book[0].side
        rnd_fills = []
        for lot in book:
            fill = tp2._settle(lot, px, ts, reason)
            fills.append(fill)
            rnd_fills.append(fill)
            cash += _margin(lot, lev) + fill.pnl
        pnl = sum(f.pnl for f in rnd_fills)
        fee = sum(f.fee for f in rnd_fills)
        rounds.append(
            Round(
                side=side,
                n_lots=len(book),
                avg=_avg_entry(book),
                exit_px=px,
                pnl=pnl,
                fee=fee,
                reason=reason,
            )
        )
        stats["n_rounds"] += 1
        if pnl > 0:
            stats["round_wins"] += 1
        open_round_side = None
        open_round_lots = 0
        return []

    def _maybe_liq(px: float, ts: int) -> bool:
        nonlocal halted
        if halted:
            return True
        eq, _ = _equity(px)
        thresh = _tot_notional() * mmr
        if eq > thresh:
            return False
        longs[:] = _close_side(longs, px, ts, "LIQUIDATED")
        shorts[:] = _close_side(shorts, px, ts, "LIQUIDATED")
        stats["liquidated"] = True
        halted = True
        _mark(px)
        return True

    def _try_add(
        side: str,
        book: list[tp2.Lot],
        px: float,
        ts: int,
        skip_key: str,
        add_key: str,
        max_key: str,
    ) -> list[tp2.Lot]:
        nonlocal nid, cash, open_round_side, open_round_lots
        if mode == "long_only" and side == "short":
            return book
        if mode == "short_only" and side == "long":
            return book
        if len(book) >= max_lots:
            stats["cap_skips"] += 1
            return book
        avg = _avg_entry(book)
        if book and ((side == "long" and px >= avg - 1e-12) or (side == "short" and px <= avg + 1e-12)):
            stats[skip_key] += 1
            return book
        eq_now, _ = _equity(px)
        notional = max(eq_now, 0.0) * size_pct
        notional = min(notional, cash * lev)
        if notional < 1e-6:
            stats["cap_skips"] += 1
            return book
        margin = notional / lev
        if cash < margin - 1e-12:
            stats["cap_skips"] += 1
            return book
        qty = notional / px
        lot = tp2.Lot(nid, side, ts, px, qty, 0.0)
        nid += 1
        cash -= margin
        book = book + [lot]
        stats[add_key] += 1
        stats[max_key] = max(stats[max_key], len(book))
        if not open_round_side:
            open_round_side = side
        open_round_lots = len(book)
        return book

    for row in m5.itertuples(index=False):
        aligned = row.aligned
        ts = int(row.ts)
        px = float(row.close)
        is_red = float(row.close) < float(row.open)
        is_green = float(row.close) > float(row.open)

        if halted:
            _mark(px)
            continue

        # Exit on 3TF flip
        if aligned == "TREND_DOWN" and longs and mode in ("regime", "long_only"):
            longs = _close_side(longs, px, ts, "TREND_DOWN")
        if aligned == "TREND_UP" and shorts and mode in ("regime", "short_only"):
            shorts = _close_side(shorts, px, ts, "TREND_UP")

        net = sum(l.qty for l in longs) - sum(l.qty for l in shorts)
        wick = float(row.low) if net > 0 else float(row.high) if net < 0 else px
        if _maybe_liq(wick, ts):
            continue

        # Regime: never hold both sides
        if mode == "regime":
            if aligned == "TREND_UP" and shorts:
                shorts = _close_side(shorts, px, ts, "REGIME_FLIP")
            if aligned == "TREND_DOWN" and longs:
                longs = _close_side(longs, px, ts, "REGIME_FLIP")

        _mark(px)

        if aligned == "TREND_UP" and is_red and mode in ("regime", "long_only"):
            longs = _try_add("long", longs, px, ts, "long_skips", "long_adds", "max_long")
        elif aligned == "TREND_DOWN" and is_green and mode in ("regime", "short_only"):
            shorts = _try_add("short", shorts, px, ts, "short_skips", "short_adds", "max_short")

        _mark(px)

    last = m5.iloc[-1]
    last_ts, last_c = int(last.ts), float(last.close)
    if not halted:
        if longs:
            longs = _close_side(longs, last_c, last_ts, "EOD_OPEN")
        if shorts:
            shorts = _close_side(shorts, last_c, last_ts, "EOD_OPEN")

    fills.sort(key=lambda f: (f.closed_at, f.id))
    stats["end_equity"] = cash
    stats["pnl"] = cash - capital
    stats["pnl_pct"] = stats["pnl"] / capital * 100
    stats["fee"] = sum(f.fee for f in fills)
    stats["rounds"] = rounds
    closed = [r for r in rounds if r.reason != "EOD_OPEN"]
    stats["wr"] = (stats["round_wins"] / len(closed) * 100) if closed else 0.0
    return fills, stats


def _summarize(m5: pd.DataFrame, results: dict) -> list[str]:
    first, last = m5.iloc[0], m5.iloc[-1]
    px_chg = (last.close / first.close - 1) * 100
    n_up = int((m5["aligned"] == "TREND_UP").sum())
    n_dn = int((m5["aligned"] == "TREND_DOWN").sum())
    n_nt = int((m5["aligned"] == "NO_TREND").sum())
    lines = [
        f"- Giá: {first.close:.4f} → {last.close:.4f} ({px_chg:+.2f}%)",
        f"- Nến TREND_UP / TREND_DOWN / NO_TREND: {n_up} / {n_dn} / {n_nt}",
        "",
        "| Mode | Cap lot | Vốn cuối | PnL | % | Round | WR | Peak lot | Max DD | Phí | Skip cap |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for mode in MODES:
        for cap in MAX_LOT_CAPS:
            st = results[(mode, cap)]
            lines.append(
                f"| {MODE_LABELS[mode]} | {cap} | **{st['end_equity']:.2f}** | "
                f"{st['pnl']:+.2f} | {st['pnl_pct']:+.1f}% | {st['n_rounds']} | "
                f"{st['wr']:.0f}% | L{st['max_long']}/S{st['max_short']} | "
                f"{st['max_dd']:+.0f} | {st['fee']:.1f} | {st['cap_skips']} |"
            )
    lines.append("")
    return lines


def main() -> None:
    lookback = max(WINDOWS)
    print(f"Fetching {lookback}d klines...", flush=True)
    frames = tp2.fetch_labeled_frames(lookback)
    all_results: dict[int, dict] = {}
    summary_lines = [
        "# Regime switch + cap lot — LINK 1k/10x/0.5%",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- **Regime:** long khi TREND_UP + nến đỏ, short khi TREND_DOWN + nến xanh; không giữ 2 chiều.",
        "- **Long-only / Short-only:** cùng entry, chỉ 1 chiều.",
        "- Skip avg đã lời · đóng khi 3 khung đảo · NO_TREND giữ lệnh không add.",
        f"- Vốn {CAPITAL:.0f} USDT · leverage {LEVERAGE:.0f}x · {SIZE_PCT*100:.1f}% equity/lệnh · phí {FEE*100:.2f}%/side",
        "",
    ]

    for days in WINDOWS:
        print(f"\n=== {days}d ===", flush=True)
        m5 = tp2.slice_m5(frames, days)
        first, last = m5.iloc[0], m5.iloc[-1]
        window_results: dict = {}
        detail_lines = [
            f"# Regime switch — {days} ngày",
            "",
            f"- Cửa sổ: {tp2._local(int(first.ts))} → {tp2._local(int(last.ts) + tp2.mtf.TF['5m'])}",
            "",
            "## Tổng hợp",
            "",
        ]

        for mode in MODES:
            for cap in MAX_LOT_CAPS:
                key = (mode, cap)
                print(f"  {mode} cap={cap}...", flush=True)
                _, st = run(m5, mode=mode, max_lots=cap)
                window_results[key] = st
                print(
                    f"    equity={st['end_equity']:.2f} pnl={st['pnl']:+.2f} "
                    f"rounds={st['n_rounds']} dd={st['max_dd']:+.0f} peak L{st['max_long']}/S{st['max_short']}"
                )

        all_results[days] = window_results
        detail_lines += _summarize(m5, window_results)

        for mode in MODES:
            for cap in MAX_LOT_CAPS:
                st = window_results[(mode, cap)]
                detail_lines += [
                    f"## {MODE_LABELS[mode]} — cap {cap} lot",
                    "",
                    f"- Long add {st['long_adds']} (skip {st['long_skips']}) · "
                    f"Short add {st['short_adds']} (skip {st['short_skips']})",
                    f"- Peak lot long {st['max_long']} / short {st['max_short']} · "
                    f"peak notional {st['max_notional']:.0f} · margin {st['max_margin']:.0f}",
                    f"- Equity min/max {st['min_equity']:.0f}/{st['max_equity']:.0f} · "
                    f"MTM tệ nhất {st['min_mtm']:+.0f}",
                    "",
                    "| # | Side | Lots | Avg | Exit | PnL | Reason |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                ]
                for i, r in enumerate(st["rounds"], 1):
                    detail_lines.append(
                        f"| {i} | {r.side} | {r.n_lots} | {r.avg:.4f} | {r.exit_px:.4f} | "
                        f"{r.pnl:+.4f} | {r.reason} |"
                    )
                detail_lines.append("")

        out = ROOT / "docs" / f"backtest_LINK_regime_switch_{days}d.md"
        out.write_text("\n".join(detail_lines) + "\n", encoding="utf-8")
        print(f"Wrote {out}")

        summary_lines += [f"## {days} ngày", ""]
        summary_lines += _summarize(m5, window_results)

    # Compare cap 5 vs 10 for regime
    summary_lines += [
        "## So sánh nhanh — Regime switch",
        "",
        "| Cửa sổ | Cap | PnL | % | Max DD | vs Long-only | vs Short-only |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for days in WINDOWS:
        wr = all_results[days]
        for cap in MAX_LOT_CAPS:
            reg = wr[("regime", cap)]
            lon = wr[("long_only", cap)]
            sho = wr[("short_only", cap)]
            summary_lines.append(
                f"| {days}d | {cap} | **{reg['pnl']:+.2f}** | {reg['pnl_pct']:+.1f}% | "
                f"{reg['max_dd']:+.0f} | {reg['pnl'] - lon['pnl']:+.2f} vs long | "
                f"{reg['pnl'] - sho['pnl']:+.2f} vs short |"
            )
    summary_lines.append("")

    summary_path = ROOT / "docs" / "backtest_LINK_regime_switch_90_365.md"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(f"\nWrote {summary_path}")


if __name__ == "__main__":
    main()

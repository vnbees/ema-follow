#!/usr/bin/env python3
"""RSI<=30 mean-reversion + hard SL. Timeout 12/24/48h, SL 1/1.5/2%."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
LOOKBACK_DAYS = 365
RSI_PERIOD = 14
ZONE_PCT = 0.0025
MOVE_AWAY_PCT = 0.0050
MARGIN_PCT = 0.01
LEVERAGE = 10.0
FEE = 0.0004
CAPITAL = 1000.0
TIMEOUT_HOURS = (12, 24, 48)
SL_PCTS = (0.0, 0.01, 0.015, 0.02)

spec = importlib.util.spec_from_file_location("rsi_rev", ROOT / "scripts" / "backtest_link_rsi_price_revisit.py")
rsi_rev = importlib.util.module_from_spec(spec)
sys.modules["rsi_rev"] = rsi_rev
spec.loader.exec_module(rsi_rev)


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def run(df, *, timeout_h: int, sl_pct: float) -> tuple[list[dict], dict]:
    timeout_bars = int(timeout_h * 60 / 5)
    event_flag = df["evt_rsi_le30"].to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    rsis = df["rsi"].to_numpy()
    tss = df["ts"].to_numpy()

    trades: list[dict] = []
    pending: dict | None = None
    open_trade: dict | None = None
    equity = CAPITAL
    curve = [equity]

    def settle(exit_px: float, ts: int, reason: str) -> None:
        nonlocal equity, open_trade, pending
        assert open_trade is not None
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
        pending = None

    for i in range(len(df)):
        px = float(closes[i])
        hi = float(highs[i])
        lo = float(lows[i])
        ts = int(tss[i])

        if open_trade is not None:
            side = open_trade["side"]
            tp = open_trade["tp"]
            sl = open_trade["sl"]
            hit_tp = (side == "long" and hi >= tp) or (side == "short" and lo <= tp)
            hit_sl = sl is not None and (
                (side == "long" and lo <= sl) or (side == "short" and hi >= sl)
            )
            timed_out = i - open_trade["entry_idx"] >= timeout_bars

            # Conservative: if SL+TP same bar, count as SL
            if hit_sl:
                settle(sl, ts, "SL")
            elif hit_tp:
                settle(tp, ts, "TP")
            elif timed_out:
                settle(px, ts, "TIMEOUT")
            curve.append(equity)
            continue

        if event_flag[i]:
            pending = {
                "anchor_ts": ts,
                "anchor_idx": i,
                "anchor_price": px,
                "anchor_rsi": float(rsis[i]),
            }

        if pending is not None and i > pending["anchor_idx"]:
            anchor = pending["anchor_price"]
            side = None
            entry = None
            tp = None
            if hi >= anchor * (1 + MOVE_AWAY_PCT):
                side = "short"
                entry = px
                tp = anchor * (1 + ZONE_PCT)
            elif lo <= anchor * (1 - MOVE_AWAY_PCT):
                side = "long"
                entry = px
                tp = anchor * (1 - ZONE_PCT)

            if side is not None:
                sl = None
                if sl_pct > 0:
                    sl = entry * (1 - sl_pct) if side == "long" else entry * (1 + sl_pct)
                margin = equity * MARGIN_PCT
                notional = margin * LEVERAGE
                qty = notional / entry
                open_trade = {
                    "id": len(trades) + 1,
                    "timeout_h": timeout_h,
                    "sl_pct": sl_pct,
                    "side": side,
                    "anchor_ts": pending["anchor_ts"],
                    "anchor_price": anchor,
                    "anchor_rsi": pending["anchor_rsi"],
                    "entry_idx": i,
                    "entry_ts": ts,
                    "entry": entry,
                    "tp": tp,
                    "sl": sl,
                    "qty": qty,
                    "notional": notional,
                }

        curve.append(equity)

    if open_trade is not None:
        settle(float(closes[-1]), int(tss[-1]), "EOD_OPEN")

    peak = curve[0]
    max_dd = 0.0
    for e in curve:
        peak = max(peak, e)
        max_dd = min(max_dd, e - peak)

    def subset(reason: str) -> list[dict]:
        return [t for t in trades if t["reason"] == reason]

    tp_t = subset("TP")
    sl_t = subset("SL")
    to_t = subset("TIMEOUT")
    eod = subset("EOD_OPEN")
    wins = [t for t in trades if t["pnl"] > 0]
    stats = {
        "timeout_h": timeout_h,
        "sl_pct": sl_pct,
        "n": len(trades),
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "tp_count": len(tp_t),
        "sl_count": len(sl_t),
        "timeout_count": len(to_t),
        "eod_count": len(eod),
        "pnl": equity - CAPITAL,
        "pnl_pct": (equity - CAPITAL) / CAPITAL * 100,
        "avg_pnl": (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0,
        "avg_tp": (sum(t["pnl"] for t in tp_t) / len(tp_t)) if tp_t else 0.0,
        "avg_sl": (sum(t["pnl"] for t in sl_t) / len(sl_t)) if sl_t else 0.0,
        "avg_timeout": (sum(t["pnl"] for t in to_t) / len(to_t)) if to_t else 0.0,
        "worst_sl": min((t["pnl"] for t in sl_t), default=0.0),
        "worst_timeout": min((t["pnl"] for t in to_t), default=0.0),
        "max_dd": max_dd,
        "equity_end": equity,
    }
    return trades, stats


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_ms = 5 * 60 * 1000
    last_closed = (now_ms // bar_ms) * bar_ms
    warmup_ms = (RSI_PERIOD + 5) * bar_ms
    fetch_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000 - warmup_ms

    print(f"Fetching LINKUSDT 5m from {_local(fetch_from)}...", flush=True)
    df = rsi_rev.smc_fetch.fetch_klines("5m", fetch_from, last_closed)
    df["rsi"] = rsi_rev.compute_rsi(df["close"], RSI_PERIOD)
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000
    df = df[df["ts"] >= window_from].copy().reset_index(drop=True)
    df = rsi_rev.mark_events(df)

    first, last = df.iloc[0], df.iloc[-1]
    px_chg = (last.close / first.close - 1) * 100
    results: list[tuple[dict, list[dict]]] = []

    for sl_pct in SL_PCTS:
        for timeout_h in TIMEOUT_HOURS:
            print(f"  SL={sl_pct*100:.1f}% timeout={timeout_h}h...", flush=True)
            trades, st = run(df, timeout_h=timeout_h, sl_pct=sl_pct)
            results.append((st, trades))
            print(
                f"    n={st['n']} WR={st['wr']:.0f}% pnl={st['pnl']:+.2f} "
                f"dd={st['max_dd']:+.2f} SL={st['sl_count']} TO={st['timeout_count']}",
                flush=True,
            )

    lines = [
        "# RSI<=30 mean reversion + hard SL — LINK 5m 365d",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cửa sổ: **{LOOKBACK_DAYS} ngày** · Giá {first.close:.4f} → {last.close:.4f} ({px_chg:+.2f}%)",
        "- Anchor: **chỉ RSI <= 30**",
        f"- Entry: giá rời xa `{MOVE_AWAY_PCT*100:.2f}%` khỏi anchor → long/short kéo về",
        f"- TP: quay lại vùng anchor `{ZONE_PCT*100:.2f}%`",
        "- SL cứng: 0% (baseline, chỉ timeout) / 1% / 1.5% / 2% từ entry",
        "- Cùng nến chạm SL+TP → tính SL (conservative)",
        f"- Size: margin {MARGIN_PCT*100:.1f}% equity · {LEVERAGE:.0f}x · phí {FEE*100:.2f}%/side",
        "",
        "## Tổng hợp",
        "",
        "| SL | Timeout | Trades | WR | TP | SL hit | Timeout | PnL | % | Avg/trade | Avg SL | Avg timeout | Worst SL | Worst timeout | Max DD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for st, _ in results:
        sl_label = "không SL" if st["sl_pct"] == 0 else f"{st['sl_pct']*100:.1f}%"
        lines.append(
            f"| {sl_label} | {st['timeout_h']}h | {st['n']} | {st['wr']:.0f}% | "
            f"{st['tp_count']} | {st['sl_count']} | {st['timeout_count']} | "
            f"**{st['pnl']:+.2f}** | **{st['pnl_pct']:+.2f}%** | {st['avg_pnl']:+.3f} | "
            f"{st['avg_sl']:+.3f} | {st['avg_timeout']:+.3f} | {st['worst_sl']:+.3f} | "
            f"{st['worst_timeout']:+.3f} | {st['max_dd']:+.2f} |"
        )

    best = max(results, key=lambda x: x[0]["pnl"])
    bst, btr = best
    sl_label = "không SL" if bst["sl_pct"] == 0 else f"{bst['sl_pct']*100:.1f}%"
    lines += [
        "",
        "## Kết luận nhanh",
        "",
        f"- Combo tốt nhất theo PnL: **SL {sl_label} + timeout {bst['timeout_h']}h**",
        f"- PnL **{bst['pnl']:+.2f} USDT ({bst['pnl_pct']:+.2f}%)** · WR {bst['wr']:.0f}% · Max DD {bst['max_dd']:+.2f}",
        f"- TP {bst['tp_count']} · SL {bst['sl_count']} · Timeout {bst['timeout_count']}",
        f"- Avg lỗ SL {bst['avg_sl']:+.3f} · Avg lỗ timeout {bst['avg_timeout']:+.3f}",
        "",
        f"## Mẫu lệnh — best (SL {sl_label}, {bst['timeout_h']}h)",
        "",
        "| # | Side | Anchor | Gia anchor | Vào | Entry | Ra | Exit | SL | TP | PnL | Reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t in btr[:25]:
        sl_px = "-" if t["sl"] is None else f"{t['sl']:.4f}"
        lines.append(
            f"| {t['id']} | {t['side']} | {_local(t['anchor_ts'])} | {t['anchor_price']:.4f} | "
            f"{_local(t['entry_ts'])} | {t['entry']:.4f} | {_local(t['exit_ts'])} | {t['exit_px']:.4f} | "
            f"{sl_px} | {t['tp']:.4f} | {t['pnl']:+.3f} | {t['reason']} |"
        )
    if len(btr) > 25:
        lines += ["", f"({len(btr) - 25} lệnh khác không hiện thị)", ""]

    # Side split for best
    longs = [t for t in btr if t["side"] == "long"]
    shorts = [t for t in btr if t["side"] == "short"]
    def _side_pnl(xs: list[dict]) -> float:
        return sum(t["pnl"] for t in xs)
    lines += [
        "",
        "### Long vs Short (best combo)",
        "",
        f"- Long: {len(longs)} lệnh · PnL {_side_pnl(longs):+.2f}",
        f"- Short: {len(shorts)} lệnh · PnL {_side_pnl(shorts):+.2f}",
        "",
    ]

    out = ROOT / "docs" / "backtest_LINK_rsi30_sl_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(
        f"BEST SL={sl_label} timeout={bst['timeout_h']}h pnl={bst['pnl']:+.2f} "
        f"wr={bst['wr']:.0f}% dd={bst['max_dd']:+.2f}"
    )


if __name__ == "__main__":
    main()

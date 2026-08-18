#!/usr/bin/env python3
"""Backtest SMC Range (Liquidity Sweep) trên LINK 5m, 30 ngày qua."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SYMBOL = "LINKUSDT"
FEE = 0.0004         # 0.04% mỗi chiều (taker)
LOOKBACK_DAYS = 365  # sẽ slice ra 30/90/365 trong main
CAPITAL = 1000.0
RISK_PCT = 0.01      # rủi ro 1% equity mỗi lệnh
LEVERAGE = 10.0
MAX_OPEN = 1         # chỉ 1 lệnh cùng lúc (chờ đóng rồi mới mở)

# ─── SMC params ───────────────────────────────────────────────────────────────
RANGE_PERIOD = 50        # nến để xác định hộp (~4h tích lũy)
MAX_RANGE_PCT = 0.02     # hộp ≤ 2% — tốt nhất trong param sweep 30d
TP_MODE = "mid"          # "mid" → box_mid | "far" → biên đối diện


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def fetch_klines(interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    bar_ms = {"5m": 5 * 60 * 1000}[interval]
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": interval,
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "smc-backtest/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.loads(resp.read().decode())
                break
            except Exception as exc:
                if attempt < 5:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        time.sleep(0.15)
        if not rows:
            break
        for r in rows:
            ts = int(r[0])
            if start_ms <= ts < end_ms:
                out.append(
                    {
                        "ts": ts,
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                    }
                )
        last_ts = int(rows[-1][0])
        nxt = last_ts + bar_ms
        if nxt <= cursor:
            break
        cursor = nxt
    return pd.DataFrame(out).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def compute_signals(df: pd.DataFrame, range_period: int, max_range_pct: float) -> pd.DataFrame:
    df = df.copy()
    df["box_high"] = df["high"].rolling(window=range_period).max().shift(1)
    df["box_low"] = df["low"].rolling(window=range_period).min().shift(1)
    df["box_mid"] = (df["box_high"] + df["box_low"]) / 2
    df["box_range_pct"] = (df["box_high"] - df["box_low"]) / df["close"]
    df["is_tight_range"] = df["box_range_pct"] < max_range_pct
    df["sweep_high"] = (df["high"] > df["box_high"]) & (df["close"] < df["box_high"])
    df["sweep_low"] = (df["low"] < df["box_low"]) & (df["close"] > df["box_low"])
    df["signal"] = np.where(
        df["is_tight_range"] & df["sweep_low"],
        1,
        np.where(df["is_tight_range"] & df["sweep_high"], -1, 0),
    )
    # SL: ngoài râu 0.2%
    df["sl"] = np.where(
        df["signal"] == 1,
        df["low"] * 0.998,
        np.where(df["signal"] == -1, df["high"] * 1.002, np.nan),
    )
    # TP mid
    df["tp_mid"] = df["box_mid"]
    # TP far: biên đối diện
    df["tp_far"] = np.where(
        df["signal"] == 1,
        df["box_high"],
        np.where(df["signal"] == -1, df["box_low"], np.nan),
    )
    return df


def run(df: pd.DataFrame, *, tp_mode: str = "mid") -> tuple[list[dict], dict]:
    """Chạy backtest, 1 lệnh tại 1 thời điểm (no overlap)."""
    trades: list[dict] = []
    open_trade: dict | None = None
    equity = CAPITAL
    equity_curve: list[float] = []

    for i, row in enumerate(df.itertuples(index=False)):
        px_o = float(row.open)
        px_h = float(row.high)
        px_l = float(row.low)
        px_c = float(row.close)
        ts = int(row.ts)

        # ── Kiểm tra đóng lệnh đang mở ──────────────────────────────────────
        if open_trade is not None:
            side = open_trade["side"]
            sl = open_trade["sl"]
            tp = open_trade["tp"]
            entry = open_trade["entry"]
            qty = open_trade["qty"]

            hit_sl = (side == "long" and px_l <= sl) or (side == "short" and px_h >= sl)
            hit_tp = (side == "long" and px_h >= tp) or (side == "short" and px_l <= tp)

            # Ưu tiên SL trước nếu cùng nến
            if hit_sl and hit_tp:
                hit_tp = False

            if hit_sl or hit_tp:
                exit_px = sl if hit_sl else tp
                reason = "SL" if hit_sl else "TP"
                if side == "long":
                    gross = (exit_px - entry) * qty
                else:
                    gross = (entry - exit_px) * qty
                fee = (entry + exit_px) * qty * FEE
                pnl = gross - fee
                equity += pnl
                trades.append(
                    {
                        **open_trade,
                        "exit_ts": ts,
                        "exit_px": exit_px,
                        "reason": reason,
                        "pnl": pnl,
                        "fee": fee,
                        "equity_after": equity,
                    }
                )
                open_trade = None

        equity_curve.append(equity)

        # ── Mở lệnh mới nếu chưa có ─────────────────────────────────────────
        if open_trade is None and int(row.signal) != 0:
            sig = int(row.signal)
            side = "long" if sig == 1 else "short"
            sl_px = float(row.sl)
            tp_px = float(row.tp_mid) if tp_mode == "mid" else float(row.tp_far)

            if np.isnan(sl_px) or np.isnan(tp_px):
                continue

            sl_dist = abs(px_c - sl_px)
            if sl_dist < 1e-12:
                continue

            risk_usdt = equity * RISK_PCT
            qty = risk_usdt / sl_dist   # USDT position size
            notional = qty * px_c
            margin = notional / LEVERAGE

            # Bỏ qua nếu margin > 50% equity (bảo vệ)
            if margin > equity * 0.5:
                continue

            open_trade = {
                "id": len(trades) + 1,
                "side": side,
                "entry_ts": ts,
                "entry": px_c,
                "sl": sl_px,
                "tp": tp_px,
                "qty": qty,
                "notional": notional,
                "margin": margin,
                "signal_sweep": "sweep_low" if sig == 1 else "sweep_high",
            }

    # EOD: đóng lệnh còn mở theo giá đóng nến cuối
    if open_trade is not None:
        last = df.iloc[-1]
        exit_px = float(last.close)
        entry = open_trade["entry"]
        qty = open_trade["qty"]
        side = open_trade["side"]
        if side == "long":
            gross = (exit_px - entry) * qty
        else:
            gross = (entry - exit_px) * qty
        fee = (entry + exit_px) * qty * FEE
        pnl = gross - fee
        equity += pnl
        trades.append(
            {
                **open_trade,
                "exit_ts": int(last.ts),
                "exit_px": exit_px,
                "reason": "EOD_OPEN",
                "pnl": pnl,
                "fee": fee,
                "equity_after": equity,
            }
        )

    # Stats
    closed = [t for t in trades if t["reason"] != "EOD_OPEN"]
    tp_trades = [t for t in closed if t["reason"] == "TP"]
    sl_trades = [t for t in closed if t["reason"] == "SL"]
    wins = [t for t in closed if t["pnl"] > 0]
    total_pnl = sum(t["pnl"] for t in trades)
    total_fee = sum(t["fee"] for t in trades)

    # Max DD
    peak = CAPITAL
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = min(max_dd, e - peak)

    stats = {
        "n_total": len(trades),
        "n_closed": len(closed),
        "n_tp": len(tp_trades),
        "n_sl": len(sl_trades),
        "n_eod": len(trades) - len(closed),
        "win_rate": len(wins) / len(closed) * 100 if closed else 0.0,
        "pnl": total_pnl,
        "fee": total_fee,
        "equity_end": equity,
        "pnl_pct": (equity - CAPITAL) / CAPITAL * 100,
        "max_dd": max_dd,
        "avg_pnl": total_pnl / len(trades) if trades else 0.0,
        "avg_tp_pnl": sum(t["pnl"] for t in tp_trades) / len(tp_trades) if tp_trades else 0.0,
        "avg_sl_pnl": sum(t["pnl"] for t in sl_trades) / len(sl_trades) if sl_trades else 0.0,
    }
    return trades, stats


def _row(label: str, value: str) -> str:
    return f"| {label} | {value} |"


def write_report(
    df: pd.DataFrame,
    trades_mid: list[dict],
    st_mid: dict,
    trades_far: list[dict],
    st_far: dict,
    path: Path,
    n_signals: int,
    n_range_bars: int,
    sweep_header: list[str] | None = None,
) -> None:
    first, last = df.iloc[0], df.iloc[-1]
    px_chg = (last.close / first.close - 1) * 100
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

    lines = [
        f"# SMC Range (Liquidity Sweep) — LINK 5m {LOOKBACK_DAYS} ngày",
        "",
        f"- Sinh lúc: {now}",
        f"- Cửa sổ: **{_local(int(first.ts))} → {_local(int(last.ts))}**",
        f"- Giá: {first.close:.4f} → {last.close:.4f} ({px_chg:+.2f}%)",
        f"- Tổng nến 5m: {len(df)} · Nến trong sideway range: {n_range_bars} ({n_range_bars/len(df)*100:.0f}%)",
        f"- Tín hiệu sweep (trước khi filter): {n_signals}",
        "",
        "## Tham số",
        "",
        f"| Tham số | Giá trị |",
        f"| --- | --- |",
        _row("range_period", f"{RANGE_PERIOD} nến (~4.2h)"),
        _row("max_range_pct", f"{MAX_RANGE_PCT*100:.0f}% biên độ"),
        _row("Risk / lệnh", f"{RISK_PCT*100:.0f}% equity"),
        _row("Leverage", f"{LEVERAGE:.0f}x"),
        _row("TP mode mid", "→ box_mid"),
        _row("TP mode far", "→ biên đối diện (box_high/low)"),
        _row("SL", "0.2% ngoài râu quét"),
        _row("Phí", f"{FEE*100:.2f}%/side (taker)"),
        "",
        "## Kết quả so sánh",
        "",
        "| | TP = box_mid | TP = biên đối diện |",
        "| --- | ---: | ---: |",
        f"| Tổng lệnh | {st_mid['n_total']} | {st_far['n_total']} |",
        f"| Đóng đúng rule | {st_mid['n_closed']} | {st_far['n_closed']} |",
        f"| TP hit | {st_mid['n_tp']} | {st_far['n_tp']} |",
        f"| SL hit | {st_mid['n_sl']} | {st_far['n_sl']} |",
        f"| EOD open | {st_mid['n_eod']} | {st_far['n_eod']} |",
        f"| Win rate | {st_mid['win_rate']:.0f}% | {st_far['win_rate']:.0f}% |",
        f"| **PnL** | **{st_mid['pnl']:+.2f} USDT** | **{st_far['pnl']:+.2f} USDT** |",
        f"| Equity cuối | {st_mid['equity_end']:.2f} | {st_far['equity_end']:.2f} |",
        f"| **% lãi/lỗ** | **{st_mid['pnl_pct']:+.1f}%** | **{st_far['pnl_pct']:+.1f}%** |",
        f"| Max DD | {st_mid['max_dd']:+.2f} | {st_far['max_dd']:+.2f} |",
        f"| Avg PnL/lệnh | {st_mid['avg_pnl']:+.2f} | {st_far['avg_pnl']:+.2f} |",
        f"| Avg TP PnL | {st_mid['avg_tp_pnl']:+.2f} | {st_far['avg_tp_pnl']:+.2f} |",
        f"| Avg SL PnL | {st_mid['avg_sl_pnl']:+.2f} | {st_far['avg_sl_pnl']:+.2f} |",
        f"| Tổng phí | {st_mid['fee']:.2f} | {st_far['fee']:.2f} |",
        "",
    ]

    if sweep_header:
        lines += sweep_header

    for label, trades, st in [("TP = box_mid", trades_mid, st_mid), ("TP = biên đối diện", trades_far, st_far)]:
        lines += [
            f"## Chi tiết lệnh — {label}",
            "",
            "| # | Side | Vào | Ra | Entry | Exit | SL | TP | PnL | Reason |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for t in trades:
            lines.append(
                f"| {t['id']} | {t['side']} | {_local(t['entry_ts'])} | {_local(t['exit_ts'])} | "
                f"{t['entry']:.4f} | {t['exit_px']:.4f} | {t['sl']:.4f} | {t['tp']:.4f} | "
                f"{t['pnl']:+.2f} | {t['reason']} |"
            )
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_window(raw: pd.DataFrame, days: int) -> dict:
    """Chạy backtest trên cửa sổ `days` ngày từ raw."""
    bar_ms = 5 * 60 * 1000
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed = (now_ms // bar_ms) * bar_ms
    window_from = last_closed - days * 24 * 3600 * 1000
    df = compute_signals(raw, RANGE_PERIOD, MAX_RANGE_PCT)
    dw = df[df["ts"] >= window_from].copy().reset_index(drop=True)
    trades_mid, st_mid = run(dw, tp_mode="mid")
    trades_far, st_far = run(dw, tp_mode="far")
    first, last_row = dw.iloc[0], dw.iloc[-1]
    return {
        "days": days,
        "df": dw,
        "trades_mid": trades_mid, "st_mid": st_mid,
        "trades_far": trades_far, "st_far": st_far,
        "px_chg": (last_row.close / first.close - 1) * 100,
        "n_bars": len(dw),
        "n_signals": int((dw["signal"] != 0).sum()),
        "n_range_bars": int(dw["is_tight_range"].sum()),
    }


def main() -> None:
    bar_ms = 5 * 60 * 1000
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed = (now_ms // bar_ms) * bar_ms
    warmup_ms = (RANGE_PERIOD + 5) * bar_ms
    fetch_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000 - warmup_ms

    print(f"Fetching LINK 5m 365d from {_local(fetch_from)}...", flush=True)
    raw = fetch_klines("5m", fetch_from, last_closed)
    print(f"  {len(raw)} bars fetched\n", flush=True)

    windows = [30, 90, 365]
    results = []
    for days in windows:
        w = _run_window(raw, days)
        results.append(w)
        sm, sf = w["st_mid"], w["st_far"]
        print(
            f"{days:>3}d | LINK {w['px_chg']:+.1f}% | "
            f"range {w['n_range_bars']/w['n_bars']*100:.0f}% | sig {w['n_signals']} | "
            f"TP_mid WR={sm['win_rate']:.0f}% PnL={sm['pnl']:+.1f}({sm['pnl_pct']:+.1f}%) DD={sm['max_dd']:+.0f} | "
            f"TP_far WR={sf['win_rate']:.0f}% PnL={sf['pnl']:+.1f}({sf['pnl_pct']:+.1f}%) DD={sf['max_dd']:+.0f}"
        )

    # ── Tổng hợp report ────────────────────────────────────────────────────────
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    summary_lines = [
        "# SMC Range (Liquidity Sweep) — LINK 5m · 30 / 90 / 365 ngày",
        "",
        f"- Sinh lúc: {now_str}",
        f"- Symbol: LINKUSDT · Khung: 5m",
        f"- `range_period={RANGE_PERIOD}` · `max_range_pct={MAX_RANGE_PCT*100:.0f}%` · Risk {RISK_PCT*100:.0f}%/lệnh · {LEVERAGE:.0f}x",
        "- SL 0.2% ngoài râu nến · TP mid = box_mid · TP far = biên đối diện",
        "- 1 lệnh tại 1 thời điểm (no overlap) · Phí 0.04%/side taker",
        "",
        "## Tổng hợp theo cửa sổ",
        "",
        "| Cửa sổ | LINK % | Range bars | Signals | TP | Trades | WR | PnL | % | Max DD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for w in results:
        sm, sf = w["st_mid"], w["st_far"]
        rb_pct = w["n_range_bars"] / w["n_bars"] * 100
        for label, st in [("mid", sm), ("far", sf)]:
            summary_lines.append(
                f"| {w['days']}d | {w['px_chg']:+.1f}% | {rb_pct:.0f}% | {w['n_signals']} | "
                f"{label} | {st['n_total']} | {st['win_rate']:.0f}% | "
                f"**{st['pnl']:+.1f}** | **{st['pnl_pct']:+.1f}%** | {st['max_dd']:+.0f} |"
            )
    summary_lines += [
        "",
        "## Giải thích",
        "",
        "- **TP mid**: chốt lợi về giữa hộp (box_mid) — RR thấp, nhiều lệnh thắng nhỏ.",
        "- **TP far**: chốt lợi về biên đối diện — RR cao (~3–12:1), WR thấp nhưng EV có thể dương.",
        "- **Max DD**: vốn xuống thấp nhất so với đỉnh trước đó, tính theo equity mark-to-market.",
        "",
    ]

    # Chi tiết từng cửa sổ
    for w in results:
        days = w["days"]
        sm, sf = w["st_mid"], w["st_far"]
        first_bar = w["df"].iloc[0]
        last_bar = w["df"].iloc[-1]
        summary_lines += [
            f"## Chi tiết {days} ngày",
            "",
            f"- Cửa sổ: {_local(int(first_bar.ts))} → {_local(int(last_bar.ts))}",
            f"- Giá: {first_bar.close:.4f} → {last_bar.close:.4f} ({w['px_chg']:+.2f}%)",
            f"- Range bars: {w['n_range_bars']}/{w['n_bars']} · Signals: {w['n_signals']}",
            "",
            "### TP = box_mid",
            f"- Trades: {sm['n_total']} · TP: {sm['n_tp']} · SL: {sm['n_sl']} · EOD: {sm['n_eod']}",
            f"- WR: {sm['win_rate']:.0f}% · PnL: **{sm['pnl']:+.2f} USDT ({sm['pnl_pct']:+.1f}%)** · DD: {sm['max_dd']:+.2f}",
            f"- Avg TP: {sm['avg_tp_pnl']:+.2f} · Avg SL: {sm['avg_sl_pnl']:+.2f} · Phí: {sm['fee']:.2f}",
            "",
            "### TP = biên đối diện",
            f"- Trades: {sf['n_total']} · TP: {sf['n_tp']} · SL: {sf['n_sl']} · EOD: {sf['n_eod']}",
            f"- WR: {sf['win_rate']:.0f}% · PnL: **{sf['pnl']:+.2f} USDT ({sf['pnl_pct']:+.1f}%)** · DD: {sf['max_dd']:+.2f}",
            f"- Avg TP: {sf['avg_tp_pnl']:+.2f} · Avg SL: {sf['avg_sl_pnl']:+.2f} · Phí: {sf['fee']:.2f}",
            "",
        ]

        # Bảng lệnh cho cửa sổ ngắn (≤90d) hoặc chỉ TP far cho 365d
        for tp_label, trades in [("TP_mid", w["trades_mid"]), ("TP_far", w["trades_far"])]:
            if days == 365 and len(trades) > 300:
                summary_lines.append(f"*(365d {tp_label}: {len(trades)} lệnh — bỏ bảng chi tiết)*\n")
                continue
            summary_lines += [
                f"#### Lệnh {tp_label} — {days}d",
                "",
                "| # | Side | Vào | Ra | Entry | Exit | SL | TP | PnL | Reason |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
            for t in trades:
                summary_lines.append(
                    f"| {t['id']} | {t['side']} | {_local(t['entry_ts'])} | {_local(t['exit_ts'])} | "
                    f"{t['entry']:.4f} | {t['exit_px']:.4f} | {t['sl']:.4f} | {t['tp']:.4f} | "
                    f"{t['pnl']:+.2f} | {t['reason']} |"
                )
            summary_lines.append("")

    out = ROOT / "docs" / "backtest_LINK_smc_90_365d.md"
    out.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()

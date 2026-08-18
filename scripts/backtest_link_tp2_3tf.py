#!/usr/bin/env python3
"""3 khung TREND_DOWN + nến xanh → short, TP 2%; đóng hết khi TREND_UP. Long ngược lại."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mtf", ROOT / "scripts" / "test_link_mtf_trend.py")
mtf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mtf)

NOTIONAL = 100.0
FEE = 0.0004
TP_PCT = 0.02
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass
class Lot:
    id: int
    side: str
    ts: int
    entry: float
    qty: float
    tp: float


@dataclass
class Fill:
    id: int
    side: str
    opened_at: int
    closed_at: int
    entry: float
    exit: float
    qty: float
    tp: float
    reason: str
    pnl: float
    fee: float
    pnl_pct: float


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def label_tf_fast(df: pd.DataFrame) -> pd.DataFrame:
    df = mtf.detect_sideway(df)
    sideway = df["is_sideway"].fillna(False).astype(bool)
    up = df["ema_fast"] > df["ema_slow"]
    down = df["ema_fast"] < df["ema_slow"]
    slope_up = df["ema_slope"] > 0
    slope_down = df["ema_slope"] < 0
    df["trend"] = np.select(
        [sideway, up & slope_up, down & slope_down, up, down],
        ["SIDEWAY", "UPTREND", "DOWNTREND", "UP_WEAK", "DOWN_WEAK"],
        default="UNCLEAR",
    )
    df["dir"] = df["trend"].map(mtf.direction)
    return df


def fetch_labeled_frames(lookback_days: int) -> dict[str, pd.DataFrame]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed = (now_ms // mtf.TF["5m"]) * mtf.TF["5m"]
    window_from = last_closed - lookback_days * 24 * 3600 * 1000
    warmup_ms = {
        "5m": 7 * 24 * 3600 * 1000,
        "1h": 20 * 24 * 3600 * 1000,
        "4h": 40 * 24 * 3600 * 1000,
    }
    frames: dict[str, pd.DataFrame] = {}
    for interval in ("5m", "1h", "4h"):
        start = window_from - warmup_ms[interval]
        print(f"fetch {interval} from {_local(start)} …", flush=True)
        raw = mtf.fetch_klines(interval, start, last_closed)
        frames[interval] = label_tf_fast(raw)
    return frames


def slice_m5(frames: dict[str, pd.DataFrame], lookback_days: int) -> pd.DataFrame:
    last_closed = int(frames["5m"]["ts"].iloc[-1]) + mtf.TF["5m"]
    window_from = last_closed - lookback_days * 24 * 3600 * 1000
    m5 = frames["5m"]
    m5 = m5[m5["ts"] >= window_from].copy()
    m5 = mtf.attach_htf(m5, frames["1h"], "h1", mtf.TF["1h"])
    m5 = mtf.attach_htf(m5, frames["4h"], "h4", mtf.TF["4h"])
    m5 = m5.dropna(subset=["h1_dir", "h4_dir"]).copy()
    up = (m5["dir"] == "UP") & (m5["h1_dir"] == "UP") & (m5["h4_dir"] == "UP")
    down = (m5["dir"] == "DOWN") & (m5["h1_dir"] == "DOWN") & (m5["h4_dir"] == "DOWN")
    m5["aligned"] = np.select([up, down], ["TREND_UP", "TREND_DOWN"], default="NO_TREND")
    return m5


def _settle(lot: Lot, exit_px: float, ts: int, reason: str) -> Fill:
    fee = (lot.entry + exit_px) * lot.qty * FEE
    if lot.side == "short":
        gross = (lot.entry - exit_px) * lot.qty
    else:
        gross = (exit_px - lot.entry) * lot.qty
    net = gross - fee
    return Fill(
        id=lot.id,
        side=lot.side,
        opened_at=lot.ts,
        closed_at=ts,
        entry=lot.entry,
        exit=exit_px,
        qty=lot.qty,
        tp=lot.tp,
        reason=reason,
        pnl=net,
        fee=fee,
        pnl_pct=net / (lot.entry * lot.qty) * 100,
    )


def _avg_entry(lots: list[Lot]) -> float:
    qty = sum(l.qty for l in lots)
    if qty <= 0:
        return 0.0
    return sum(l.entry * l.qty for l in lots) / qty


def run(
    m5: pd.DataFrame,
    *,
    skip_if_avg_profit: bool,
    exit_on_trend_end: bool = False,
) -> tuple[list[Fill], dict]:
    longs: list[Lot] = []
    shorts: list[Lot] = []
    fills: list[Fill] = []
    nid = 1
    stats = {
        "long_adds": 0,
        "short_adds": 0,
        "long_skips": 0,
        "short_skips": 0,
        "max_long": 0,
        "max_short": 0,
        "skip_if_avg_profit": skip_if_avg_profit,
        "exit_on_trend_end": exit_on_trend_end,
    }

    for row in m5.itertuples(index=False):
        aligned = row.aligned
        ts = int(row.ts)
        o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close)
        is_red = c < o
        is_green = c > o

        keep_s = []
        for lot in shorts:
            if l <= lot.tp:
                fills.append(_settle(lot, lot.tp, ts, "TP_2PCT"))
            else:
                keep_s.append(lot)
        shorts = keep_s
        keep_l = []
        for lot in longs:
            if h >= lot.tp:
                fills.append(_settle(lot, lot.tp, ts, "TP_2PCT"))
            else:
                keep_l.append(lot)
        longs = keep_l

        if exit_on_trend_end:
            if shorts and aligned != "TREND_DOWN":
                for lot in shorts:
                    fills.append(_settle(lot, c, ts, "TREND_END"))
                shorts = []
            if longs and aligned != "TREND_UP":
                for lot in longs:
                    fills.append(_settle(lot, c, ts, "TREND_END"))
                longs = []
        else:
            if aligned == "TREND_UP" and shorts:
                for lot in shorts:
                    fills.append(_settle(lot, c, ts, "TREND_UP"))
                shorts = []
            if aligned == "TREND_DOWN" and longs:
                for lot in longs:
                    fills.append(_settle(lot, c, ts, "TREND_DOWN"))
                longs = []

        if aligned == "TREND_DOWN" and is_green:
            avg = _avg_entry(shorts)
            if skip_if_avg_profit and shorts and c <= avg + 1e-12:
                stats["short_skips"] += 1
            else:
                tp = c * (1 - TP_PCT)
                shorts.append(Lot(nid, "short", ts, c, NOTIONAL / c, tp))
                nid += 1
                stats["short_adds"] += 1
                stats["max_short"] = max(stats["max_short"], len(shorts))
        if aligned == "TREND_UP" and is_red:
            avg = _avg_entry(longs)
            if skip_if_avg_profit and longs and c >= avg - 1e-12:
                stats["long_skips"] += 1
            else:
                tp = c * (1 + TP_PCT)
                longs.append(Lot(nid, "long", ts, c, NOTIONAL / c, tp))
                nid += 1
                stats["long_adds"] += 1
                stats["max_long"] = max(stats["max_long"], len(longs))

    last = m5.iloc[-1]
    last_ts, last_c = int(last.ts), float(last.close)
    for lot in longs:
        fills.append(_settle(lot, last_c, last_ts, "EOD_OPEN"))
    for lot in shorts:
        fills.append(_settle(lot, last_c, last_ts, "EOD_OPEN"))
    fills.sort(key=lambda f: (f.closed_at, f.id))
    return fills, stats


def _summ(fills: list[Fill], side: str | None = None) -> dict:
    xs = [f for f in fills if side is None or f.side == side]
    closed = [f for f in xs if f.reason != "EOD_OPEN"]
    tp = [f for f in xs if f.reason == "TP_2PCT"]
    force = [f for f in xs if f.reason in ("TREND_UP", "TREND_DOWN", "TREND_END")]
    eod = [f for f in xs if f.reason == "EOD_OPEN"]
    wins = [f for f in closed if f.pnl > 0]
    force_wins = [f for f in force if f.pnl > 0]
    return {
        "n": len(xs),
        "closed": len(closed),
        "tp": len(tp),
        "rev": len(force),
        "eod": len(eod),
        "pnl": sum(f.pnl for f in xs),
        "pnl_closed": sum(f.pnl for f in closed),
        "pnl_eod": sum(f.pnl for f in eod),
        "pnl_tp": sum(f.pnl for f in tp),
        "pnl_force": sum(f.pnl for f in force),
        "fee": sum(f.fee for f in xs),
        "wins": len(wins),
        "wr": (len(wins) / len(closed) * 100) if closed else 0.0,
        "wr_force": (len(force_wins) / len(force) * 100) if force else 0.0,
    }


def _block(title: str, fills: list[Fill], stats: dict) -> list[str]:
    all_s = _summ(fills)
    long_s = _summ(fills, "long")
    short_s = _summ(fills, "short")
    exit_col = "Hết trend" if stats.get("exit_on_trend_end") else "Đảo 3 khung"
    return [
        f"## {title}",
        "",
        f"- Long add {stats['long_adds']} (skip {stats['long_skips']}, peak {stats['max_long']} lot) · "
        f"Short add {stats['short_adds']} (skip {stats['short_skips']}, peak {stats['max_short']} lot)",
        f"- **PnL tổng: {all_s['pnl']:+.4f} USDT** (đóng {all_s['pnl_closed']:+.4f} · EOD {all_s['pnl_eod']:+.4f} · phí {all_s['fee']:.4f})",
        f"- TP {all_s['pnl_tp']:+.2f} · thoát sớm {all_s['pnl_force']:+.2f} (WR thoát {all_s['wr_force']:.0f}%)",
        f"- Long: {long_s['pnl']:+.4f} (TP {long_s['tp']}, thoát {long_s['rev']}, EOD {long_s['eod']}) · "
        f"WR đóng {long_s['wins']}/{long_s['closed']} = {long_s['wr']:.0f}%",
        f"- Short: {short_s['pnl']:+.4f} (TP {short_s['tp']}, thoát {short_s['rev']}, EOD {short_s['eod']})",
        "",
        f"| Side | Lots | TP 2% | {exit_col} | EOD | PnL | WR đóng |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        f"| long | {long_s['n']} | {long_s['tp']} | {long_s['rev']} | {long_s['eod']} | {long_s['pnl']:+.4f} | {long_s['wr']:.0f}% |",
        f"| short | {short_s['n']} | {short_s['tp']} | {short_s['rev']} | {short_s['eod']} | {short_s['pnl']:+.4f} | {short_s['wr']:.0f}% |",
        f"| **tổng** | **{all_s['n']}** | {all_s['tp']} | {all_s['rev']} | {all_s['eod']} | **{all_s['pnl']:+.4f}** | {all_s['wr']:.0f}% |",
        "",
    ]


def _write_window(
    days: int,
    m5: pd.DataFrame,
    fills_rev: list[Fill],
    st_rev: dict,
    fills_end: list[Fill],
    st_end: dict,
) -> dict:
    first, last = m5.iloc[0], m5.iloc[-1]
    a, b = _summ(fills_rev), _summ(fills_end)
    labels = {7: "7 ngày", 90: "3 tháng", 180: "6 tháng", 365: "1 năm"}
    label = labels[days]
    path = ROOT / "docs" / f"backtest_LINK_tp2pct_trendend_{days}d.md"
    lines = [
        f"# TP 2% — đóng khi hết trend vs đợi đảo chiều ({label})",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cửa sổ: **{_local(int(first.ts))} → {_local(int(last.ts) + mtf.TF['5m'])}**",
        f"- Giá: {first.close:.4f} → {last.close:.4f} ({(last.close / first.close - 1) * 100:+.2f}%)",
        f"- 3 khung hiện tại: **{last.aligned}**",
        "",
        "## Rule",
        "",
        "Add: TREND_UP + nến đỏ → long TP 2%; TREND_DOWN + nến xanh → short TP 2%. "
        "**Không add khi avg đã lời.**",
        "",
        "- A: đóng hết khi 3 khung **đảo chiều** (long đợi TREND_DOWN, short đợi TREND_UP). Giữ qua NO_TREND.",
        "- B: đóng hết khi 3 khung **hết trend** (long thoát ngay NO_TREND/TREND_DOWN).",
        "",
        "## So sánh (cùng skip avg lời)",
        "",
        "| | A Đợi đảo chiều | **B Hết trend thì đóng** |",
        "| --- | --- | --- |",
        f"| Số lot | {a['n']} | **{b['n']}** |",
        f"| Peak long / short | {st_rev['max_long']} / {st_rev['max_short']} | "
        f"**{st_end['max_long']} / {st_end['max_short']}** |",
        f"| TP 2% | {a['tp']} ({a['pnl_tp']:+.0f}) | {b['tp']} ({b['pnl_tp']:+.0f}) |",
        f"| Thoát sớm | {a['rev']} ({a['pnl_force']:+.0f}) | {b['rev']} ({b['pnl_force']:+.0f}) |",
        f"| EOD còn mở | {a['eod']} | {b['eod']} |",
        f"| PnL tổng | {a['pnl']:+.2f} | **{b['pnl']:+.2f}** |",
        f"| WR đóng | {a['wr']:.0f}% | {b['wr']:.0f}% |",
        f"| WR thoát sớm | {a['wr_force']:.0f}% | {b['wr_force']:.0f}% |",
        f"| Phí | {a['fee']:.2f} | {b['fee']:.2f} |",
        "",
    ]
    lines += _block("A — Đợi đảo chiều (giữ qua NO_TREND)", fills_rev, st_rev)
    lines += _block("B — Hết trend thì đóng", fills_end, st_end)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"{days}d REV pnl={a['pnl']:+.2f} peak={st_rev['max_long']}/{st_rev['max_short']} | "
        f"END pnl={b['pnl']:+.2f} peak={st_end['max_long']}/{st_end['max_short']} "
        f"tp={b['tp']} force={b['rev']} force_pnl={b['pnl_force']:+.0f} eod={b['eod']}",
        flush=True,
    )
    return {
        "days": days,
        "label": label,
        "from": _local(int(first.ts)),
        "to": _local(int(last.ts) + mtf.TF["5m"]),
        "px_chg": (last.close / first.close - 1) * 100,
        "a": a,
        "b": b,
        "st_rev": st_rev,
        "st_end": st_end,
        "path": path,
    }


def main() -> None:
    windows = (7, 90, 180, 365)
    frames = fetch_labeled_frames(max(windows))
    results = []
    for days in windows:
        m5 = slice_m5(frames, days)
        fills_rev, st_rev = run(m5, skip_if_avg_profit=True, exit_on_trend_end=False)
        fills_end, st_end = run(m5, skip_if_avg_profit=True, exit_on_trend_end=True)
        results.append(_write_window(days, m5, fills_rev, st_rev, fills_end, st_end))

    summary = ROOT / "docs" / "backtest_LINK_tp2pct_trendend_90_180_365.md"
    lines = [
        "# TP 2% — đóng khi hết trend vs đợi đảo chiều",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Add: skip khi avg đã lời. Long TREND_UP + nến đỏ, short TREND_DOWN + nến xanh, TP 2%.",
        "- **Hết trend** = 3 khung không còn TREND_UP (long) / TREND_DOWN (short), kể cả NO_TREND.",
        "",
        "## PnL — hết trend thì đóng",
        "",
        "| Cửa sổ | Giá | Lots | Peak L/S | TP | Thoát | EOD | PnL | TP PnL | Thoát PnL |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        b, st = r["b"], r["st_end"]
        lines.append(
            f"| {r['label']} ({r['from'][:10]}→{r['to'][:10]}) | "
            f"{r['px_chg']:+.1f}% | {b['n']} | {st['max_long']}/{st['max_short']} | "
            f"{b['tp']} | {b['rev']} | {b['eod']} | **{b['pnl']:+.0f}** | "
            f"{b['pnl_tp']:+.0f} | {b['pnl_force']:+.0f} |"
        )
    lines += [
        "",
        "## PnL — đợi đảo chiều (bản cũ, skip avg)",
        "",
        "| Cửa sổ | Lots | Peak L/S | TP | Đảo | EOD | PnL | TP PnL | Đảo PnL |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        a, st = r["a"], r["st_rev"]
        lines.append(
            f"| {r['label']} | {a['n']} | {st['max_long']}/{st['max_short']} | "
            f"{a['tp']} | {a['rev']} | {a['eod']} | **{a['pnl']:+.0f}** | "
            f"{a['pnl_tp']:+.0f} | {a['pnl_force']:+.0f} |"
        )
    lines += ["", "Chi tiết:"]
    for r in results:
        lines.append(f"- [{r['path'].name}]({r['path'].name})")
    lines.append("")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {summary}")


if __name__ == "__main__":
    main()

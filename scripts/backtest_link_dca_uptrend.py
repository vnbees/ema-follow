#!/usr/bin/env python3
"""DCA long nến đỏ khi 3 khung TREND_UP; đóng khi TREND_DOWN."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mtf", ROOT / "scripts" / "test_link_mtf_trend.py")
mtf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mtf)

NOTIONAL = 100.0
FEE = 0.0004
LOOKBACK_DAYS = 365
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
REPORT = ROOT / "docs" / "backtest_LINK_dca_red_uptrend_365d.md"


@dataclass
class Lot:
    ts: int
    entry: float
    qty: float


def _avg(lots: list[Lot]) -> tuple[float, float]:
    qty = sum(l.qty for l in lots)
    notional = sum(l.entry * l.qty for l in lots)
    return (notional / qty if qty else 0.0), qty


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def _close_round(lots, exit_px, exit_ts, reason, round_id, *, side: str):
    avg, qty = _avg(lots)
    fee = sum((l.entry + exit_px) * l.qty * FEE for l in lots)
    if side == "short":
        gross = (avg - exit_px) * qty
    else:
        gross = (exit_px - avg) * qty
    net = gross - fee
    return {
        "id": round_id,
        "side": side,
        "n_lots": len(lots),
        "qty": qty,
        "avg": avg,
        "first_ts": lots[0].ts,
        "last_add_ts": lots[-1].ts,
        "exit_ts": exit_ts,
        "exit": exit_px,
        "reason": reason,
        "pnl": net,
        "fee": fee,
        "pnl_pct": net / (avg * qty) * 100 if avg * qty else 0.0,
        "lots": list(lots),
    }


def run(m5: pd.DataFrame, *, side: str, exit_on: str):
    """side: long|short. exit_on: TREND_DOWN|TREND_UP|5m_DOWN|5m_UP."""
    lots: list[Lot] = []
    rounds = []
    skips = 0
    adds = 0
    equity_pts = []
    round_id = 1
    max_lots = 0
    max_notional = 0.0

    for row in m5.itertuples(index=False):
        aligned = row.aligned
        d5 = row.dir
        px = float(row.close)
        ts = int(row.ts)
        is_red = float(row.close) < float(row.open)
        is_green = float(row.close) > float(row.open)

        should_exit = False
        if lots:
            if exit_on == "TREND_DOWN" and aligned == "TREND_DOWN":
                should_exit = True
            if exit_on == "TREND_UP" and aligned == "TREND_UP":
                should_exit = True
            if exit_on == "5m_DOWN" and d5 == "DOWN":
                should_exit = True
            if exit_on == "5m_UP" and d5 == "UP":
                should_exit = True

        if should_exit:
            rnd = _close_round(lots, px, ts, exit_on, round_id, side=side)
            rounds.append(rnd)
            round_id += 1
            lots = []

        want_add = False
        if side == "long" and aligned == "TREND_UP" and is_red and not should_exit:
            avg, _ = _avg(lots)
            if lots and px >= avg - 1e-12:
                skips += 1
            else:
                want_add = True
        elif side == "short" and aligned == "TREND_DOWN" and is_green and not should_exit:
            avg, _ = _avg(lots)
            if lots and px <= avg + 1e-12:
                skips += 1
            else:
                want_add = True

        if want_add:
            qty = NOTIONAL / px
            lots.append(Lot(ts=ts, entry=px, qty=qty))
            adds += 1
            max_lots = max(max_lots, len(lots))
            max_notional = max(max_notional, sum(l.entry * l.qty for l in lots))

        if lots:
            avg, qty = _avg(lots)
            u = (avg - px) * qty if side == "short" else (px - avg) * qty
            equity_pts.append(u + sum(r["pnl"] for r in rounds))
        else:
            equity_pts.append(sum(r["pnl"] for r in rounds))

    last = m5.iloc[-1]
    if lots:
        rnd = _close_round(
            lots, float(last.close), int(last.ts), "EOD_OPEN", round_id, side=side
        )
        rounds.append(rnd)

    peak = 0.0
    max_dd = 0.0
    for e in equity_pts:
        peak = max(peak, e)
        max_dd = min(max_dd, e - peak)

    return {
        "rounds": rounds,
        "skips": skips,
        "adds": adds,
        "max_dd": max_dd,
        "max_lots": max_lots,
        "max_notional": max_notional,
        "equity_end": sum(r["pnl"] for r in rounds),
        "exit_on": exit_on,
        "side": side,
    }


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


def _prepare_m5() -> pd.DataFrame:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed = (now_ms // mtf.TF["5m"]) * mtf.TF["5m"]
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000
    warmup_ms = {
        "5m": 10 * 24 * 3600 * 1000,
        "1h": 40 * 24 * 3600 * 1000,
        "4h": 60 * 24 * 3600 * 1000,
    }
    frames = {}
    for interval in ("5m", "1h", "4h"):
        start = window_from - warmup_ms[interval]
        print(f"fetch {interval}...", flush=True)
        raw = mtf.fetch_klines(interval, start, last_closed)
        print(f"  {len(raw)} bars", flush=True)
        frames[interval] = label_tf_fast(raw)
    m5 = frames["5m"]
    m5 = m5[m5["ts"] >= window_from].copy()
    m5 = mtf.attach_htf(m5, frames["1h"], "h1", mtf.TF["1h"])
    m5 = mtf.attach_htf(m5, frames["4h"], "h4", mtf.TF["4h"])
    m5 = m5.dropna(subset=["h1_dir", "h4_dir"]).copy()
    up = (m5["dir"] == "UP") & (m5["h1_dir"] == "UP") & (m5["h4_dir"] == "UP")
    down = (m5["dir"] == "DOWN") & (m5["h1_dir"] == "DOWN") & (m5["h4_dir"] == "DOWN")
    m5["aligned"] = np.select([up, down], ["TREND_UP", "TREND_DOWN"], default="NO_TREND")
    return m5


def _md_result(title: str, res: dict) -> list[str]:
    rounds = res["rounds"]
    closed = [r for r in rounds if r["reason"] != "EOD_OPEN"]
    eod = [r for r in rounds if r["reason"] == "EOD_OPEN"]
    pnl = sum(r["pnl"] for r in rounds)
    fee = sum(r["fee"] for r in rounds)
    wins = [r for r in rounds if r["pnl"] > 0]
    lines = [
        f"## {title}",
        "",
        f"- Điều kiện thoát: `{res['exit_on']}`",
        f"- Số lần add: {res['adds']} · skip vì avg đã lời: {res['skips']}",
        f"- Số round (chuỗi DCA → đóng): {len(rounds)} "
        f"(đóng theo rule {len(closed)}, EOD {len(eod)})",
        f"- PnL tổng: **{pnl:+.4f} USDT** · phí {fee:.4f}",
        f"- Round lời/lỗ: {len(wins)}/{len(rounds) - len(wins)}"
        + (f" ({len(wins) / len(rounds) * 100:.0f}%)" if rounds else ""),
        f"- Peak lots / notional: {res['max_lots']} lot ≈ {res['max_notional']:.0f} USDT",
        f"- Max DD (mark-to-market theo nến): {res['max_dd']:+.4f} USDT",
        "",
        "| # | Lots | Avg vào | Ra | Từ | Đến | PnL | % | Reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rounds:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(r["id"]),
                    str(r["n_lots"]),
                    f"{r['avg']:.4f}",
                    f"{r['exit']:.4f}",
                    _local(r["first_ts"]),
                    _local(r["exit_ts"] + mtf.TF["5m"]),
                    f"{r['pnl']:+.4f}",
                    f"{r['pnl_pct']:+.3f}%",
                    r["reason"],
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _lot_table(res: dict, title: str, entry_desc: str) -> list[str]:
    n_lots = sum(len(r["lots"]) for r in res["rounds"])
    lines = [f"## {title}", "", f"{entry_desc}", ""]
    if n_lots > 200:
        lines += [f"({n_lots} lot — bỏ bảng chi tiết, xem round ở trên.)", ""]
        return lines
    lines += [
        "| Round | Vào (UTC+7) | Entry | Qty |",
        "| --- | --- | --- | --- |",
    ]
    for r in res["rounds"]:
        for lot in r["lots"]:
            lines.append(f"| {r['id']} | {_local(lot.ts)} | {lot.entry:.4f} | {lot.qty:.4f} |")
    lines.append("")
    return lines


def main() -> None:
    m5 = _prepare_m5()
    first, last = m5.iloc[0], m5.iloc[-1]
    long_main = run(m5, side="long", exit_on="TREND_DOWN")
    long_5m = run(m5, side="long", exit_on="5m_DOWN")
    short_main = run(m5, side="short", exit_on="TREND_UP")
    short_5m = run(m5, side="short", exit_on="5m_UP")

    header = [
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cửa sổ: **{_local(int(first.ts))} → {_local(int(last.ts) + mtf.TF['5m'])}**",
        f"- Giá: {first.close:.4f} → {last.close:.4f} ({(last.close / first.close - 1) * 100:+.2f}%)",
        f"- 3 khung hiện tại: **{last.aligned}** (5m {last.trend} / 1h {last.h1_trend} / 4h {last.h4_trend})",
        "",
    ]

    down_lines = [
        "# DCA nến xanh khi TREND_DOWN — LINK 5m 1 năm",
        "",
        *header,
        "## Rule (đối xứng long)",
        "",
        "1. Chỉ khi **5m + 1h + 4h = TREND_DOWN**.",
        "2. Nến 5m **xanh** (`close > open`) → short 100 USDT.",
        "3. Đã có vị thế và **close ≤ avg entry** (short đã lời) → không short thêm.",
        "4. **Chỉ đóng** khi 3 khung thành **TREND_UP** (biến thể: 5m đảo UP).",
        "5. `NO_TREND`: giữ lệnh, không add.",
        "6. Phí 0.04%/side. Không hedge với long trong test này (chạy độc lập).",
        "",
    ]
    n_up = sum(1 for r in short_main["rounds"] if r["reason"] == "TREND_UP")
    n_eod = sum(1 for r in short_main["rounds"] if r["reason"] == "EOD_OPEN")
    down_lines += _md_result("Kết quả rule chính — đóng khi 3 khung TREND_UP", short_main)
    notes = []
    if n_up:
        notes.append(f"Có **{n_up}** lần đóng đúng TREND_UP 3 khung.")
    if n_eod:
        notes.append("Round cuối **chưa đóng** — mark `EOD_OPEN`.")
    if not short_main["rounds"]:
        notes.append("**Không có TREND_DOWN 3 khung** trong 1 năm → không vào short lần nào.")
    if notes:
        down_lines += [" ".join(notes), ""]
    down_lines += _md_result("Biến thể — đóng khi 5m đảo UP (vẫn chỉ add lúc TREND_DOWN)", short_5m)
    down_lines += _lot_table(
        short_main,
        "Chi tiết từng lot (rule chính: thoát TREND_UP)",
        "Notional 100 USDT / lot. Vào = close nến xanh.",
    )
    down_lines += [
        "## Ghép với chiều long (cùng 1 năm, hai sổ độc lập)",
        "",
        f"- Long đóng TREND_DOWN: **{long_main['equity_end']:+.4f} USDT**",
        f"- Short đóng TREND_UP: **{short_main['equity_end']:+.4f} USDT**",
        f"- **Tổng hai chiều (rule 3 khung): {(long_main['equity_end'] + short_main['equity_end']):+.4f} USDT**",
        f"- Long thoát 5m DOWN: {long_5m['equity_end']:+.4f} · Short thoát 5m UP: {short_5m['equity_end']:+.4f} · "
        f"tổng {(long_5m['equity_end'] + short_5m['equity_end']):+.4f} USDT",
        "",
    ]
    down_path = ROOT / "docs" / "backtest_LINK_dca_green_downtrend_365d.md"
    down_path.write_text("\n".join(down_lines) + "\n", encoding="utf-8")

    # refresh long report too
    long_lines = [
        "# DCA nến đỏ khi TREND_UP — LINK 5m 1 năm",
        "",
        *header,
        "## Rule",
        "",
        "1. Chỉ khi **5m + 1h + 4h = TREND_UP**.",
        "2. Nến 5m **đỏ** → mua 100 USDT.",
        "3. Avg đã lời → không mua thêm.",
        "4. Chỉ đóng khi 3 khung **TREND_DOWN**.",
        "",
    ]
    long_lines += _md_result("Kết quả rule chính — đóng khi 3 khung TREND_DOWN", long_main)
    long_lines += _md_result("Biến thể — đóng khi 5m đảo DOWN", long_5m)
    REPORT.write_text("\n".join(long_lines) + "\n", encoding="utf-8")

    print(f"Wrote {down_path} and {REPORT}")
    print(
        f"LONG TREND_DOWN: adds={long_main['adds']} pnl={long_main['equity_end']:+.4f} "
        f"rounds={len(long_main['rounds'])} dd={long_main['max_dd']:+.4f} peak={long_main['max_lots']}"
    )
    for r in long_main["rounds"]:
        print(
            f"  L{r['id']} lots={r['n_lots']} avg={r['avg']:.4f} exit={r['exit']:.4f} "
            f"{r['reason']} {r['pnl']:+.4f}"
        )
    print(
        f"SHORT TREND_UP: adds={short_main['adds']} pnl={short_main['equity_end']:+.4f} "
        f"rounds={len(short_main['rounds'])} dd={short_main['max_dd']:+.4f} peak={short_main['max_lots']}"
    )
    for r in short_main["rounds"]:
        print(
            f"  S{r['id']} lots={r['n_lots']} avg={r['avg']:.4f} exit={r['exit']:.4f} "
            f"{r['reason']} {r['pnl']:+.4f}"
        )
    print(
        f"5m-exit long={long_5m['equity_end']:+.4f} ({len(long_5m['rounds'])}r) "
        f"short={short_5m['equity_end']:+.4f} ({len(short_5m['rounds'])}r)"
    )
    print(
        f"COMBINED 3tf={long_main['equity_end'] + short_main['equity_end']:+.4f} "
        f"5m-exit={long_5m['equity_end'] + short_5m['equity_end']:+.4f}"
    )


if __name__ == "__main__":
    main()

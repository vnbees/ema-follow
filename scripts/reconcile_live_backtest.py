#!/usr/bin/env python3
"""Đối chiếu lệnh Donchian live (bot.db) vs backtest LIVE cùng window."""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
BAR_MS = 15 * 60 * 1000
CAPITAL = 1000.0


@dataclass
class Trade:
    source: str
    symbol: str
    side: str
    entry_ts: int
    exit_ts: int | None
    entry_px: float
    exit_px: float | None
    pnl: float | None
    status: str
    lot_id: int | None = None

    @property
    def bar_ts(self) -> int:
        return (self.entry_ts // BAR_MS) * BAR_MS


def parse_date(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=TZ).timestamp() * 1000)


def load_live(db_path: Path, from_ts: int) -> tuple[list[Trade], list[Trade], dict]:
    from_iso = datetime.fromtimestamp(from_ts / 1000, tz=TZ).astimezone(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, symbol, side, entry_ts, entry_px, close_ts, close_px, pnl_usdt, status,
               opened_at, closed_at
        FROM donchian_lots
        WHERE opened_at >= ? OR (closed_at IS NOT NULL AND closed_at >= ?)
        ORDER BY COALESCE(entry_ts, 0), id
        """,
        (from_iso, from_iso),
    )
    rows = cur.fetchall()

    closed: list[Trade] = []
    open_trades: list[Trade] = []
    for r in rows:
        t = Trade(
            source="live",
            lot_id=r["id"],
            symbol=r["symbol"],
            side=r["side"],
            entry_ts=int(r["entry_ts"] or 0),
            exit_ts=int(r["close_ts"]) if r["close_ts"] else None,
            entry_px=float(r["entry_px"]),
            exit_px=float(r["close_px"]) if r["close_px"] else None,
            pnl=float(r["pnl_usdt"]) if r["pnl_usdt"] is not None else None,
            status=r["status"],
        )
        if t.status == "closed":
            closed.append(t)
        else:
            open_trades.append(t)

    cur.execute(
        "SELECT recorded_at, equity FROM equity_snapshots WHERE recorded_at >= ? ORDER BY recorded_at",
        (from_iso,),
    )
    snaps = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT equity FROM equity_snapshots WHERE recorded_at < ? ORDER BY recorded_at DESC LIMIT 1",
        (from_iso,),
    )
    row = cur.fetchone()
    eq_before = float(row["equity"]) if row else None
    return closed, open_trades, {"snaps": snaps, "eq_before": eq_before, "from_iso": from_iso}


def fetch_marks(symbols: list[str]) -> dict[str, float]:
    req = urllib.request.Request("https://fapi.binance.com/fapi/v1/ticker/price", headers={"User-Agent": "reconcile/1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        all_px = {r["symbol"]: float(r["price"]) for r in json.loads(resp.read())}
    return {s: all_px[s] for s in symbols if s in all_px}


def unrealized(side: str, entry: float, mark: float, size: float) -> float:
    if side == "long":
        return (mark - entry) * size
    return (entry - mark) * size


def replay_backtest(from_ts: int, to_ts: int | None) -> list[Trade]:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("bt_srv", ROOT / "scripts/backtest_sr_volume_3y.py")
    bt = importlib.util.module_from_spec(spec)
    sys.modules["bt_srv"] = bt
    spec.loader.exec_module(bt)

    hunt = bt._load("hunt_rec", "scripts/backtest_hunt_pct_per_day.py")
    bflip = bt._load("bflip_rec", "scripts/backtest_donchian_20coin_breadth_flip.py")
    dfs = bt.load_cache(hunt, bflip.SYMBOLS_20, from_ts=from_ts, to_ts=to_ts, min_bars=bt.WARMUP_BARS + 100)

    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)

    cash = CAPITAL
    opens: list[dict] = []
    out: list[Trade] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}
    trade_id = 0

    def stack(sym: str) -> int:
        return sum(1 for t in opens if t["sym"] == sym)

    def close_pos(t: dict, px: float, ts_close: int) -> None:
        nonlocal cash, trade_id
        pnl = hunt._pnl(t["side"], t["entry"], px, t["qty"])
        cash += t["margin"] + pnl
        out.append(
            Trade(
                source="backtest",
                lot_id=trade_id,
                symbol=t["sym"],
                side=t["side"],
                entry_ts=t["entry_ts"],
                exit_ts=ts_close,
                entry_px=t["entry"],
                exit_px=px,
                pnl=pnl,
                status="closed",
            )
        )
        trade_id += 1

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        still = []
        for t in opens:
            b = bar[t["sym"]]
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
            side = t["side"]
            tp = up if side == "long" else dn
            if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                close_pos(t, tp, ts)
            else:
                still.append(t)
        opens = still

        bar_trends: dict[str, str | None] = {}
        raw_cands: list[dict] = []
        for sym in symbols:
            b = bar[sym]
            if np.isnan(b["dc_middle"]):
                bar_trends[sym] = None
                continue
            px, o = float(b["close"]), float(b["open"])
            mid = float(b["dc_middle"])
            bar_trends[sym] = "up" if px > mid else "down"
            if np.isnan(b["dc_upper"]) or np.isnan(b["atr"]):
                continue
            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
            w, a = float(b["dc_width"]), float(b["atr"])
            pe, par = bool(b["parallel_exit"]), bool(b["bands_parallel"])
            st = state[sym]
            if pe:
                st["trend"] = "up" if px > mid else "down"
                st["waiting"] = True
            if st["waiting"] and st["trend"] and stack(sym) == 0:
                counter = (st["trend"] == "up" and px < o) or (st["trend"] == "down" and px > o)
                if counter and not par and w > 1e-12:
                    side = "long" if st["trend"] == "up" else "short"
                    tp_near = up if side == "long" else dn
                    sl_opp = dn if side == "long" else up
                    pot = abs(tp_near - px) / max(abs(px - sl_opp), 1e-12)
                    body = abs(px - o) / a if a > 0 else 0.0
                    if 0.3 <= body <= 1.2 and pot >= 0.5:
                        raw_cands.append(
                            {"sym": sym, "side": side, "px": px, "pot": pot, "body": body, "up": up, "dn": dn}
                        )

        raw_cands.sort(key=lambda x: x["pot"], reverse=True)
        vote = bflip.breadth_vote(bar_trends, 1.3, 12)
        entries: list[dict] = []
        for cand in raw_cands:
            if vote is None or cand["side"] == vote:
                entries.append(cand)
            else:
                fc = bflip.flipped_candidate(cand, cand["up"], cand["dn"], cand["px"])
                if fc:
                    entries.append(fc)
        entries.sort(key=lambda x: x["pot"], reverse=True)

        for cand in entries:
            if len(opens) >= 20 or stack(cand["sym"]) > 0:
                continue
            sm = float(np.clip(0.5 + cand["pot"], 0.5, 2.0))
            eq = max(cash + sum(t["margin"] for t in opens), 0.0)
            notional = min(eq * 0.01 * hunt.LEVERAGE * sm, cash * hunt.LEVERAGE)
            if notional < 1e-6:
                continue
            margin = notional / hunt.LEVERAGE
            if cash < margin - 1e-12:
                continue
            cash -= margin
            opens.append(
                {
                    "sym": cand["sym"],
                    "side": cand["side"],
                    "entry": cand["px"],
                    "qty": notional / cand["px"],
                    "margin": margin,
                    "entry_ts": ts,
                }
            )
            state[cand["sym"]]["waiting"] = False
        for sym in symbols:
            if stack(sym) > 0:
                state[sym]["waiting"] = False

    last_ts = common[-1]
    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    for t in opens:
        px = last_mark[t["sym"]]
        pnl = hunt._pnl(t["side"], t["entry"], px, t["qty"])
        out.append(
            Trade(
                source="backtest",
                lot_id=trade_id,
                symbol=t["sym"],
                side=t["side"],
                entry_ts=t["entry_ts"],
                exit_ts=None,
                entry_px=t["entry"],
                exit_px=px,
                pnl=pnl,
                status="open",
            )
        )
        trade_id += 1
    return out


def match_trades(live_closed: list[Trade], bt_closed: list[Trade]) -> list[dict]:
    used_bt: set[int] = set()
    rows: list[dict] = []
    for lv in live_closed:
        best = None
        best_key = None
        for j, bt in enumerate(bt_closed):
            if j in used_bt:
                continue
            if bt.symbol != lv.symbol or bt.side != lv.side:
                continue
            bar_diff = abs(bt.bar_ts - lv.bar_ts) // BAR_MS
            if bar_diff > 1:
                continue
            key = bar_diff
            if best is None or key < best_key:
                best, best_key = (j, bt), key
        if best:
            j, bt = best
            used_bt.add(j)
            rows.append(
                {
                    "match": "ok",
                    "symbol": lv.symbol,
                    "side": lv.side,
                    "live_bar": lv.bar_ts,
                    "bt_bar": bt.bar_ts,
                    "live_entry": lv.entry_px,
                    "bt_entry": bt.entry_px,
                    "live_pnl": lv.pnl,
                    "bt_pnl": bt.pnl,
                    "pnl_diff": (lv.pnl or 0) - (bt.pnl or 0),
                    "live_lot": lv.lot_id,
                }
            )
        else:
            rows.append(
                {
                    "match": "live_only",
                    "symbol": lv.symbol,
                    "side": lv.side,
                    "live_bar": lv.bar_ts,
                    "live_entry": lv.entry_px,
                    "live_pnl": lv.pnl,
                    "live_lot": lv.lot_id,
                }
            )
    for j, bt in enumerate(bt_closed):
        if j in used_bt:
            continue
        if bt.entry_ts >= parse_date("2026-08-24"):
            rows.append(
                {
                    "match": "bt_only",
                    "symbol": bt.symbol,
                    "side": bt.side,
                    "bt_bar": bt.bar_ts,
                    "bt_entry": bt.entry_px,
                    "bt_pnl": bt.pnl,
                }
            )
    return rows


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=TZ).strftime("%m-%d %H:%M")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default="2026-08-24")
    parser.add_argument("--db", default=str(ROOT / "data" / "bot.db"))
    args = parser.parse_args()

    from_ts = parse_date(args.from_date)
    db_path = Path(args.db)

    print(f"=== Đối chiếu LIVE vs BACKTEST từ {args.from_date} (VN) ===\n")
    print(f"Nguồn live: {db_path}")

    live_closed, live_open, meta = load_live(db_path, from_ts)
    bt_all = replay_backtest(from_ts, None)
    bt_closed = [t for t in bt_all if t.status == "closed" and (t.entry_ts >= from_ts or (t.exit_ts or 0) >= from_ts)]
    bt_open = [t for t in bt_all if t.status == "open"]

    # filter live closed in window
    live_closed_win = [t for t in live_closed if (t.exit_ts or 0) >= from_ts or t.entry_ts >= from_ts]

    marks = fetch_marks(sorted({t.symbol for t in live_open} | {t.symbol for t in bt_open}))

    live_realized = sum(t.pnl or 0 for t in live_closed_win)
    live_unreal = 0.0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for t in live_open:
        row = conn.execute("SELECT size FROM donchian_lots WHERE id=?", (t.lot_id,)).fetchone()
        size = float(row["size"]) if row else 0.0
        mk = marks.get(t.symbol, t.entry_px)
        live_unreal += unrealized(t.side, t.entry_px, mk, size)

    bt_realized = sum(t.pnl or 0 for t in bt_closed if t.entry_ts >= from_ts)
    bt_unreal = sum(t.pnl or 0 for t in bt_open)

    print(f"\n--- Tổng quan PnL ---")
    print(f"{'':28s} {'Live':>12s} {'Backtest':>12s}")
    print(f"{'Realized (đóng)':28s} {live_realized:+12.2f} {bt_realized:+12.2f}")
    print(f"{'Unrealized (mở)':28s} {live_unreal:+12.2f} {bt_unreal:+12.2f}")
    print(f"{'TỔNG':28s} {live_realized+live_unreal:+12.2f} {bt_realized+bt_unreal:+12.2f}")
    print(f"{'Số lệnh đóng':28s} {len(live_closed_win):12d} {len([t for t in bt_closed if t.entry_ts>=from_ts]):12d}")
    print(f"{'Số lệnh mở':28s} {len(live_open):12d} {len(bt_open):12d}")

    if meta["eq_before"]:
        eq_start = meta["eq_before"]
        eq_snap_end = meta["snaps"][-1]["equity"] if meta["snaps"] else eq_start
        eq_live_mtm = eq_snap_end + (live_realized - sum(
            t.pnl or 0 for t in live_closed if (t.exit_ts or 0) >= from_ts and t.entry_ts < from_ts
        ))  # rough
        print(f"\nEquity snapshot trước 24/8: ${meta['eq_before']:.2f}")
        if meta["snaps"]:
            print(f"Equity snapshot cuối DB:    ${meta['snaps'][-1]['equity']:.2f} @ {meta['snaps'][-1]['recorded_at'][:19]}")
        print(f"Equity ước tính + mark open: ${eq_snap_end + live_unreal - (eq_snap_end - eq_start - live_realized):.2f} (approx)")
        print(f"  → Live MTM vs đầu kỳ: {(live_realized + live_unreal) / eq_start * 100:+.2f}% trên ${eq_start:.0f}")

    matches = match_trades(live_closed_win, [t for t in bt_closed if t.entry_ts >= from_ts])
    ok = [m for m in matches if m["match"] == "ok"]
    live_only = [m for m in matches if m["match"] == "live_only"]
    bt_only = [m for m in matches if m["match"] == "bt_only"]

    print(f"\n--- Khớp lệnh ĐÓNG (cùng symbol/side, ±1 bar 15m) ---")
    print(f"Khớp: {len(ok)} | Chỉ live: {len(live_only)} | Chỉ backtest: {len(bt_only)}")
    if ok:
        print(f"\n{'Symbol':10s} {'Side':5s} {'LivePnL':>9s} {'BTPnL':>9s} {'Δ':>8s}  bar")
        for m in sorted(ok, key=lambda x: abs(x.get("pnl_diff", 0)), reverse=True)[:20]:
            print(
                f"{m['symbol']:10s} {m['side']:5s} {m['live_pnl']:+9.2f} {m['bt_pnl']:+9.2f} "
                f"{m['pnl_diff']:+8.2f}  {fmt_ts(m['live_bar'])}"
            )

    if live_only:
        print(f"\n--- Chỉ LIVE có (backtest không vào) ---")
        for m in live_only:
            print(f"  lot#{m['live_lot']} {m['symbol']:10s} {m['side']:5s} pnl={m['live_pnl']:+.2f} @ {fmt_ts(m['live_bar'])}")

    if bt_only:
        print(f"\n--- Chỉ BACKTEST có (live không vào) — top 15 theo |pnl| ---")
        for m in sorted(bt_only, key=lambda x: abs(x.get("bt_pnl", 0)), reverse=True)[:15]:
            print(f"  {m['symbol']:10s} {m['side']:5s} pnl={m['bt_pnl']:+.2f} @ {fmt_ts(m['bt_bar'])}")

    print(f"\n--- Lệnh MỞ live (đang float) ---")
    for t in live_open:
        row = conn.execute("SELECT size, margin_usdt FROM donchian_lots WHERE id=?", (t.lot_id,)).fetchone()
        size = float(row["size"])
        margin = float(row["margin_usdt"])
        mk = marks.get(t.symbol, t.entry_px)
        pnl = unrealized(t.side, t.entry_px, mk, size)
        bt_same = next((b for b in bt_open if b.symbol == t.symbol and b.side == t.side), None)
        flag = "có trong BT" if bt_same else "BT không mở"
        print(
            f"  #{t.lot_id} {t.symbol:10s} {t.side:5s} entry={t.entry_px:.4f} now={mk:.4f} "
            f"pnl={pnl:+.2f} ({pnl/margin*100:+.0f}% margin) opened {fmt_ts(t.entry_ts)} | {flag}"
        )

    print(f"\n--- Lệnh MỞ backtest (không có trên live) ---")
    live_open_keys = {(t.symbol, t.side) for t in live_open}
    for t in bt_open:
        if (t.symbol, t.side) not in live_open_keys:
            print(f"  {t.symbol:10s} {t.side:5s} entry={t.entry_px:.4f} unreal={t.pnl:+.2f} @ {fmt_ts(t.entry_ts)}")

    print(f"\n⚠ DB local dừng ~09:30 sáng 24/8 — không có lệnh server 25–28/8.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

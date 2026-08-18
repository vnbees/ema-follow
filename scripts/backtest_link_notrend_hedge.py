#!/usr/bin/env python3
"""NO_TREND: vào long+short. Đóng từng chân TP 2% hoặc đóng hết khi 3 khung có trend."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tp2", ROOT / "scripts" / "backtest_link_tp2_3tf.py")
tp2 = importlib.util.module_from_spec(spec)
sys.modules["tp2"] = tp2
spec.loader.exec_module(tp2)

NOTIONAL = 100.0
FEE = 0.0004
TP_PCT = 0.02
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
WINDOWS = (7, 90, 180, 365)
LABELS = {7: "7 ngày", 90: "3 tháng", 180: "6 tháng", 365: "1 năm"}


def _add(
    side: str, ts: int, px: float, nid: int, notional: float, tp_pct: float
) -> tuple[tp2.Lot, int]:
    if side == "long":
        tp = px * (1 + tp_pct)
    else:
        tp = px * (1 - tp_pct)
    return tp2.Lot(nid, side, ts, px, notional / px, tp), nid + 1


def _tp_reason(tp_pct: float) -> str:
    return f"TP_{int(round(tp_pct * 100))}PCT"


def run(
    m5,
    *,
    mode: str,
    notional: float = NOTIONAL,
    capital: float | None = None,
    leverage: float | None = None,
    size_pct: float | None = None,
    mmr: float = 0.004,
    monthly_profit_take: bool = False,
    tp_pct: float = TP_PCT,
) -> tuple[list[tp2.Fill], dict]:
    """mode: pair | scale. size_pct = notional / equity mỗi lệnh. leverage = notional/margin."""
    longs: list[tp2.Lot] = []
    shorts: list[tp2.Lot] = []
    fills: list[tp2.Fill] = []
    nid = 1
    cash = float(capital) if capital is not None else None
    halted = False
    lev = float(leverage) if leverage else 1.0
    stats = {
        "mode": mode,
        "notional": notional,
        "capital": capital,
        "leverage": leverage,
        "size_pct": size_pct,
        "long_adds": 0,
        "short_adds": 0,
        "long_skips": 0,
        "short_skips": 0,
        "cap_skips": 0,
        "max_long": 0,
        "max_short": 0,
        "max_both": 0,
        "max_notional": 0.0,
        "max_margin": 0.0,
        "n_no_trend": 0,
        "n_trend": 0,
        "hedge_opens": 0,
        "max_dd": 0.0,
        "min_equity": float(capital) if capital is not None else 0.0,
        "max_equity": float(capital) if capital is not None else 0.0,
        "min_mtm": 0.0,
        "liquidated": False,
        "end_cash": 0.0,
        "end_equity": 0.0,
        "monthly_checks": 0,
        "monthly_closes": 0,
        "monthly_skips": 0,
        "monthly_pnl": 0.0,
        "tp_pct": tp_pct,
    }
    realized = 0.0
    peak_eq = float(capital) if capital is not None else 0.0

    def _n(lot: tp2.Lot) -> float:
        return lot.entry * lot.qty

    def _margin(lot: tp2.Lot) -> float:
        return _n(lot) / lev

    def _locked() -> float:
        return sum(_margin(x) for x in longs + shorts)

    def _tot_notional() -> float:
        return sum(_n(x) for x in longs + shorts)

    def _mtm(book: list[tp2.Lot], px: float) -> float:
        total = 0.0
        for lot in book:
            if lot.side == "short":
                gross = (lot.entry - px) * lot.qty
            else:
                gross = (px - lot.entry) * lot.qty
            total += gross - (lot.entry + px) * lot.qty * FEE
        return total

    def _equity(px: float) -> tuple[float, float]:
        mtm = _mtm(longs, px) + _mtm(shorts, px)
        if capital is None:
            return realized + mtm, mtm
        return cash + _locked() + mtm, mtm

    def _mark(px: float) -> None:
        eq, mtm = _equity(px)
        nonlocal peak_eq
        peak_eq = max(peak_eq, eq)
        stats["max_dd"] = min(stats["max_dd"], eq - peak_eq)
        stats["min_equity"] = min(stats["min_equity"], eq)
        stats["max_equity"] = max(stats["max_equity"], eq)
        stats["min_mtm"] = min(stats["min_mtm"], mtm)
        stats["max_notional"] = max(stats["max_notional"], _tot_notional())
        stats["max_margin"] = max(stats["max_margin"], _locked())

    def _close_lot(lot: tp2.Lot, px: float, ts: int, reason: str) -> None:
        nonlocal realized, cash
        fill = tp2._settle(lot, px, ts, reason)
        fills.append(fill)
        realized += fill.pnl
        if cash is not None:
            cash += _margin(lot) + fill.pnl

    def _maybe_liq(px: float, ts: int) -> bool:
        nonlocal halted
        if capital is None or halted:
            return False
        eq, _ = _equity(px)
        thresh = _tot_notional() * mmr if leverage else 0.0
        if eq > thresh:
            return False
        for lot in longs + shorts:
            _close_lot(lot, px, ts, "LIQUIDATED")
        longs.clear()
        shorts.clear()
        stats["liquidated"] = True
        halted = True
        _mark(px)
        return True

    prev_month: tuple[int, int] | None = None

    for row in m5.itertuples(index=False):
        aligned = row.aligned
        ts = int(row.ts)
        h, l, c = float(row.high), float(row.low), float(row.close)

        if halted:
            _mark(c)
            continue

        keep_s = []
        for lot in shorts:
            if l <= lot.tp:
                _close_lot(lot, lot.tp, ts, _tp_reason(tp_pct))
            else:
                keep_s.append(lot)
        shorts = keep_s
        keep_l = []
        for lot in longs:
            if h >= lot.tp:
                _close_lot(lot, lot.tp, ts, _tp_reason(tp_pct))
            else:
                keep_l.append(lot)
        longs = keep_l

        if aligned != "NO_TREND":
            stats["n_trend"] += 1
            if shorts:
                for lot in shorts:
                    _close_lot(lot, c, ts, "HAS_TREND")
                shorts = []
            if longs:
                for lot in longs:
                    _close_lot(lot, c, ts, "HAS_TREND")
                longs = []

        if leverage:
            net = sum(x.qty for x in longs) - sum(x.qty for x in shorts)
            wick = l if net > 0 else h if net < 0 else c
            if _maybe_liq(wick, ts):
                continue
        elif _maybe_liq(c, ts):
            continue

        cur_month = datetime.fromtimestamp(ts / 1000, TZ).strftime("%Y-%m")
        if monthly_profit_take and prev_month is not None and cur_month != prev_month:
            stats["monthly_checks"] += 1
            open_mtm = _mtm(longs, c) + _mtm(shorts, c)
            if (longs or shorts) and open_mtm > 0:
                stats["monthly_closes"] += 1
                for lot in list(longs) + list(shorts):
                    fill = tp2._settle(lot, c, ts, "MONTH_PROFIT")
                    fills.append(fill)
                    realized += fill.pnl
                    stats["monthly_pnl"] += fill.pnl
                    if cash is not None:
                        cash += _margin(lot) + fill.pnl
                longs, shorts = [], []
            elif longs or shorts:
                stats["monthly_skips"] += 1
        prev_month = cur_month

        _mark(c)

        if aligned != "NO_TREND":
            continue

        stats["n_no_trend"] += 1

        def try_add(side: str, book: list[tp2.Lot], skip_key: str, add_key: str, max_key: str) -> list[tp2.Lot]:
            nonlocal nid, cash
            avg = tp2._avg_entry(book)
            if mode == "scale" and book and (
                (side == "long" and c >= avg - 1e-12) or (side == "short" and c <= avg + 1e-12)
            ):
                stats[skip_key] += 1
                return book
            if size_pct is not None:
                eq_now, _ = _equity(c)
                n = max(eq_now, 0.0) * size_pct
                if cash is not None:
                    n = min(n, cash * lev)
                if n < 1e-6:
                    stats["cap_skips"] += 1
                    return book
            else:
                n = notional
            margin = n / lev
            if cash is not None and cash < margin - 1e-12:
                stats["cap_skips"] += 1
                return book
            lot, nid = _add(side, ts, c, nid, n, tp_pct)
            if cash is not None:
                cash -= margin
            book = book + [lot]
            stats[add_key] += 1
            stats[max_key] = max(stats[max_key], len(book))
            return book

        if mode == "pair":
            if not longs and not shorts:
                longs = try_add("long", longs, "long_skips", "long_adds", "max_long")
                shorts = try_add("short", shorts, "short_skips", "short_adds", "max_short")
                stats["hedge_opens"] += 1
        else:
            n0 = stats["long_adds"] + stats["short_adds"]
            longs = try_add("long", longs, "long_skips", "long_adds", "max_long")
            shorts = try_add("short", shorts, "short_skips", "short_adds", "max_short")
            if stats["long_adds"] + stats["short_adds"] > n0:
                stats["hedge_opens"] += 1

        stats["max_both"] = max(stats["max_both"], len(longs) + len(shorts))
        _mark(c)

    last = m5.iloc[-1]
    last_ts, last_c = int(last.ts), float(last.close)
    if not halted:
        for lot in list(longs) + list(shorts):
            _close_lot(lot, last_c, last_ts, "EOD_OPEN")
        longs, shorts = [], []
    fills.sort(key=lambda f: (f.closed_at, f.id))
    stats["end_cash"] = float(cash) if cash is not None else realized
    stats["end_equity"] = float(cash) if cash is not None else realized
    return fills, stats


def _summ(fills: list[tp2.Fill], side: str | None = None) -> dict:
    xs = [f for f in fills if side is None or f.side == side]
    closed = [f for f in xs if f.reason != "EOD_OPEN"]
    tp = [f for f in xs if f.reason.startswith("TP_")]
    trend = [f for f in xs if f.reason in ("HAS_TREND", "LIQUIDATED")]
    monthly = [f for f in xs if f.reason == "MONTH_PROFIT"]
    eod = [f for f in xs if f.reason == "EOD_OPEN"]
    wins = [f for f in closed if f.pnl > 0]
    return {
        "n": len(xs),
        "closed": len(closed),
        "tp": len(tp),
        "trend": len(trend),
        "monthly": len(monthly),
        "eod": len(eod),
        "pnl": sum(f.pnl for f in xs),
        "pnl_tp": sum(f.pnl for f in tp),
        "pnl_trend": sum(f.pnl for f in trend),
        "pnl_monthly": sum(f.pnl for f in monthly),
        "pnl_eod": sum(f.pnl for f in eod),
        "fee": sum(f.fee for f in xs),
        "wins": len(wins),
        "wr": (len(wins) / len(closed) * 100) if closed else 0.0,
        "wr_trend": (sum(1 for f in trend if f.pnl > 0) / len(trend) * 100) if trend else 0.0,
    }


def _block(title: str, fills: list[tp2.Fill], stats: dict) -> list[str]:
    all_s = _summ(fills)
    long_s = _summ(fills, "long")
    short_s = _summ(fills, "short")
    return [
        f"## {title}",
        "",
        f"- Nến NO_TREND {stats['n_no_trend']} · có trend {stats['n_trend']} · lần mở hedge {stats['hedge_opens']}",
        f"- Long add {stats['long_adds']} (skip {stats['long_skips']}, peak {stats['max_long']}) · "
        f"Short add {stats['short_adds']} (skip {stats['short_skips']}, peak {stats['max_short']}) · "
        f"peak 2 chân {stats['max_both']} lot ≈ {stats['max_both'] * stats.get('notional', NOTIONAL):.0f} USDT",
        f"- **PnL tổng: {all_s['pnl']:+.4f} USDT** · TP {all_s['pnl_tp']:+.2f} · hết trend {all_s['pnl_trend']:+.2f} · "
        f"EOD {all_s['pnl_eod']:+.2f} · phí {all_s['fee']:.2f}",
        f"- Equity min/max {stats['min_equity']:+.0f}/{stats['max_equity']:+.0f} · "
        f"Max DD {stats['max_dd']:+.0f} · MTM tệ nhất {stats['min_mtm']:+.0f}",
        f"- Long {long_s['pnl']:+.4f} (TP {long_s['tp']}, trend {long_s['trend']}, EOD {long_s['eod']}) · "
        f"Short {short_s['pnl']:+.4f} (TP {short_s['tp']}, trend {short_s['trend']}, EOD {short_s['eod']})",
        "",
        "| Side | Lots | TP 2% | Có trend | EOD | PnL | WR đóng |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        f"| long | {long_s['n']} | {long_s['tp']} | {long_s['trend']} | {long_s['eod']} | {long_s['pnl']:+.4f} | {long_s['wr']:.0f}% |",
        f"| short | {short_s['n']} | {short_s['tp']} | {short_s['trend']} | {short_s['eod']} | {short_s['pnl']:+.4f} | {short_s['wr']:.0f}% |",
        f"| **tổng** | **{all_s['n']}** | {all_s['tp']} | {all_s['trend']} | {all_s['eod']} | **{all_s['pnl']:+.4f}** | {all_s['wr']:.0f}% |",
        "",
    ]


def _write_window(days: int, m5, pair_fills, pair_st, scale_fills, scale_st) -> dict:
    first, last = m5.iloc[0], m5.iloc[-1]
    a, b = _summ(pair_fills), _summ(scale_fills)
    path = ROOT / "docs" / f"backtest_LINK_notrend_hedge_{days}d.md"
    n_nt = int((m5["aligned"] == "NO_TREND").sum())
    n_up = int((m5["aligned"] == "TREND_UP").sum())
    n_dn = int((m5["aligned"] == "TREND_DOWN").sum())
    lines = [
        f"# NO_TREND hedge 2 chiều — {LABELS[days]}",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cửa sổ: **{tp2._local(int(first.ts))} → {tp2._local(int(last.ts) + tp2.mtf.TF['5m'])}**",
        f"- Giá: {first.close:.4f} → {last.close:.4f} ({(last.close / first.close - 1) * 100:+.2f}%)",
        f"- Nến: NO_TREND {n_nt} · TREND_UP {n_up} · TREND_DOWN {n_dn}",
        "",
        "## Rule",
        "",
        "Chỉ vào khi **3 khung = NO_TREND**. Mỗi lần vào mở **long 100 + short 100** (cùng giá close). "
        "Từng chân chốt **TP 2%**. Còn lại đóng hết khi 3 khung thành TREND_UP hoặc TREND_DOWN. "
        "Không vào khi đang có trend.",
        "",
        "- A: **1 cặp** — chỉ mở khi cả hai sổ trống (không chồng lot).",
        "- B: **Scale** — mỗi nến NO_TREND add thêm 2 chân, **skip chân nào avg đã lời**.",
        "",
        "## So sánh",
        "",
        "| | A 1 cặp | **B Scale skip avg lời** |",
        "| --- | --- | --- |",
        f"| Lots | {a['n']} | **{b['n']}** |",
        f"| Peak 2 chân | {pair_st['max_both']} | **{scale_st['max_both']}** |",
        f"| TP 2% | {a['tp']} ({a['pnl_tp']:+.0f}) | {b['tp']} ({b['pnl_tp']:+.0f}) |",
        f"| Đóng vì có trend | {a['trend']} ({a['pnl_trend']:+.0f}) | {b['trend']} ({b['pnl_trend']:+.0f}) |",
        f"| EOD | {a['eod']} | {b['eod']} |",
        f"| PnL tổng | {a['pnl']:+.2f} | **{b['pnl']:+.2f}** |",
        f"| WR đóng | {a['wr']:.0f}% | {b['wr']:.0f}% |",
        f"| Phí | {a['fee']:.2f} | {b['fee']:.2f} |",
        "",
    ]
    lines += _block("A — 1 cặp long+short khi sổ trống", pair_fills, pair_st)
    lines += _block("B — Scale mỗi nến NO_TREND, skip avg lời", scale_fills, scale_st)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"{days}d PAIR pnl={a['pnl']:+.2f} n={a['n']} peak={pair_st['max_both']} | "
        f"SCALE pnl={b['pnl']:+.2f} n={b['n']} peak={scale_st['max_both']} "
        f"dd={scale_st['max_dd']:+.0f} mtm={scale_st['min_mtm']:+.0f} "
        f"tp={b['tp']}/{b['pnl_tp']:+.0f} trend={b['trend']}/{b['pnl_trend']:+.0f}",
        flush=True,
    )
    return {
        "days": days,
        "label": LABELS[days],
        "from": tp2._local(int(first.ts)),
        "to": tp2._local(int(last.ts) + tp2.mtf.TF["5m"]),
        "px_chg": (last.close / first.close - 1) * 100,
        "n_nt": n_nt,
        "n_up": n_up,
        "n_dn": n_dn,
        "a": a,
        "b": b,
        "pair_st": pair_st,
        "scale_st": scale_st,
        "path": path,
    }


def main() -> None:
    frames = tp2.fetch_labeled_frames(max(WINDOWS))
    results = []
    for days in WINDOWS:
        m5 = tp2.slice_m5(frames, days)
        pair_fills, pair_st = run(m5, mode="pair")
        scale_fills, scale_st = run(m5, mode="scale")
        results.append(_write_window(days, m5, pair_fills, pair_st, scale_fills, scale_st))

    summary = ROOT / "docs" / "backtest_LINK_notrend_hedge_90_180_365.md"
    lines = [
        "# NO_TREND hedge 2 chiều — 7 ngày / 3 tháng / 6 tháng / 1 năm",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Vào khi 3 khung **NO_TREND**: long+short 100 USDT. Chốt TP 2% từng chân, còn lại đóng khi **có trend**.",
        "",
        "## A — 1 cặp (không chồng)",
        "",
        "| Cửa sổ | Giá | Lots | Peak | TP | Có trend | PnL | TP PnL | Trend PnL |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        a, st = r["a"], r["pair_st"]
        lines.append(
            f"| {r['label']} ({r['from'][:10]}→{r['to'][:10]}) | {r['px_chg']:+.1f}% | "
            f"{a['n']} | {st['max_both']} | {a['tp']} | {a['trend']} | **{a['pnl']:+.0f}** | "
            f"{a['pnl_tp']:+.0f} | {a['pnl_trend']:+.0f} |"
        )
    lines += [
        "",
        "## B — Scale skip avg lời",
        "",
        "| Cửa sổ | Lots | Peak 2 chân | Max DD | MTM tệ | PnL | TP PnL | Trend PnL | Phí |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        b, st = r["b"], r["scale_st"]
        lines.append(
            f"| {r['label']} | {b['n']} | {st['max_both']} (~{st['max_both'] * NOTIONAL:.0f} USDT) | "
            f"{st['max_dd']:+.0f} | {st['min_mtm']:+.0f} | **{b['pnl']:+.0f}** | {b['pnl_tp']:+.0f} | "
            f"{b['pnl_trend']:+.0f} | {b['fee']:.0f} |"
        )
    lines += ["", "Chi tiết:"]
    for r in results:
        lines.append(f"- [{r['path'].name}]({r['path'].name})")
    lines.append("")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {summary}")


def main_cap100() -> None:
    """Vốn 100 USDT, mỗi lệnh 1 USDT, không mở thêm khi hết tiền mặt, equity <= 0 thì halt."""
    frames = tp2.fetch_labeled_frames(max(WINDOWS))
    path = ROOT / "docs" / "backtest_LINK_notrend_hedge_cap100.md"
    lines = [
        "# NO_TREND hedge — vốn 100 USDT, 1 USDT / lệnh",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Ký quỹ 1:1 (không đòn bẩy): mỗi lot khóa 1 USDT, hết tiền mặt thì không add. Equity ≤ 0 → đóng hết, dừng.",
        "- Scale: skip chân avg đã lời. 1 cặp: chỉ 2 lot khi sổ trống.",
        "",
        "## Scale (1 USDT/lệnh, trần vốn 100)",
        "",
        "| Cửa sổ | Giá | Vốn cuối | PnL | % | Peak lot | Skip hết vốn | Max DD | Equity min | Phí | Liquidated |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    pair_rows = []
    for days in WINDOWS:
        m5 = tp2.slice_m5(frames, days)
        first, last = m5.iloc[0], m5.iloc[-1]
        px = (last.close / first.close - 1) * 100
        fills, st = run(m5, mode="scale", notional=1.0, capital=100.0)
        s = _summ(fills)
        end_eq = st["end_equity"]
        pnl = end_eq - 100.0
        liq = "yes" if st["liquidated"] else "no"
        lines.append(
            f"| {LABELS[days]} ({tp2._local(int(first.ts))[:10]}→{tp2._local(int(last.ts))[:10]}) | "
            f"{px:+.1f}% | **{end_eq:.2f}** | {pnl:+.2f} | {pnl:+.1f}% | "
            f"{st['max_both']} | {st['cap_skips']} | {st['max_dd']:+.2f} | "
            f"{st['min_equity']:.2f} | {s['fee']:.2f} | {liq} |"
        )
        print(
            f"{days}d SCALE cap100 equity={end_eq:.2f} pnl={pnl:+.2f} peak={st['max_both']} "
            f"cap_skip={st['cap_skips']} dd={st['max_dd']:+.2f} min={st['min_equity']:.2f} "
            f"liq={st['liquidated']} lots={s['n']} tp={s['tp']} fee={s['fee']:.2f}",
            flush=True,
        )
        pfills, pst = run(m5, mode="pair", notional=1.0, capital=100.0)
        ps = _summ(pfills)
        pair_rows.append(
            f"| {LABELS[days]} | **{pst['end_equity']:.2f}** | {pst['end_equity']-100:+.2f} | "
            f"{pst['max_both']} | {ps['n']} | {ps['tp']} | {ps['fee']:.2f} |"
        )
    lines += [
        "",
        "## 1 cặp (1 USDT long + 1 USDT short)",
        "",
        "| Cửa sổ | Vốn cuối | PnL | Peak | Lots | TP | Phí |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *pair_rows,
        "",
        "Ghi chú: Binance futures thường min notional ~5 USDT — 1 USDT/lệnh là giả lập kích thước, không chắc đặt được live.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


def main_lev1000() -> None:
    """Vốn 1000 USDT, 10x, mỗi lệnh notional = 0.5% equity hiện tại."""
    frames = tp2.fetch_labeled_frames(max(WINDOWS))
    path = ROOT / "docs" / "backtest_LINK_notrend_hedge_1k_10x.md"
    cap = 1000.0
    lines = [
        "# NO_TREND hedge — vốn 1000 USDT, 10x, 0.5% cap / lệnh",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Vốn ban đầu **1000 USDT**, đòn bẩy **10x** (ký quỹ = notional/10, hedge 2 chân đều khóa margin).",
        "- Mỗi lệnh notional = **0.5% equity hiện tại** (lúc 1000 USDT → 5 USDT/lệnh, margin 0.50). "
        "Sizing theo vốn: lãi thì lot to hơn, lỗ thì nhỏ hơn.",
        "- Hết buying power thì không add. Thanh lý nếu equity ≤ 0.4% tổng notional (MMR) hoặc wick ngược net position.",
        "- Scale: skip chân avg đã lời. 1 cặp: chỉ mở khi sổ trống.",
        "",
        "## Scale",
        "",
        "| Cửa sổ | Giá | Vốn cuối | PnL | % | Peak lot | Peak notional | Max DD | Equity min | Phí | Liq |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    pair_rows = []
    for days in WINDOWS:
        m5 = tp2.slice_m5(frames, days)
        first, last = m5.iloc[0], m5.iloc[-1]
        px = (last.close / first.close - 1) * 100
        fills, st = run(
            m5, mode="scale", capital=cap, leverage=10.0, size_pct=0.005
        )
        s = _summ(fills)
        end_eq = st["end_equity"]
        pnl = end_eq - cap
        liq = "yes" if st["liquidated"] else "no"
        lines.append(
            f"| {LABELS[days]} ({tp2._local(int(first.ts))[:10]}→{tp2._local(int(last.ts))[:10]}) | "
            f"{px:+.1f}% | **{end_eq:.2f}** | {pnl:+.2f} | {pnl / cap * 100:+.1f}% | "
            f"{st['max_both']} | {st['max_notional']:.0f} | {st['max_dd']:+.2f} | "
            f"{st['min_equity']:.2f} | {s['fee']:.2f} | {liq} |"
        )
        print(
            f"{days}d SCALE 1k/10x equity={end_eq:.2f} pnl={pnl:+.2f} ({pnl/cap*100:+.1f}%) "
            f"peak={st['max_both']} notional={st['max_notional']:.0f} margin={st['max_margin']:.0f} "
            f"dd={st['max_dd']:+.2f} min={st['min_equity']:.2f} cap_skip={st['cap_skips']} "
            f"liq={st['liquidated']} lots={s['n']} tp={s['tp']} fee={s['fee']:.2f}",
            flush=True,
        )
        pfills, pst = run(
            m5, mode="pair", capital=cap, leverage=10.0, size_pct=0.005
        )
        ps = _summ(pfills)
        pair_rows.append(
            f"| {LABELS[days]} | **{pst['end_equity']:.2f}** | {pst['end_equity']-cap:+.2f} | "
            f"{(pst['end_equity']-cap)/cap*100:+.2f}% | {pst['max_both']} | {ps['n']} | "
            f"{ps['tp']} | {ps['fee']:.2f} | {'yes' if pst['liquidated'] else 'no'} |"
        )
    lines += [
        "",
        "## 1 cặp (long+short, 0.5% cap mỗi chân)",
        "",
        "| Cửa sổ | Vốn cuối | PnL | % | Peak | Lots | TP | Phí | Liq |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *pair_rows,
        "",
        "Ghi chú: 0.5% của 1000 = 5 USDT notional ≈ min Binance LINK. Cross 10x, MMR 0.4%.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


def main_monthly_lev1000() -> None:
    """So sánh scale 1k/10x/0.5%: không chốt tháng vs chốt hết nếu MTM lệnh mở > 0."""
    frames = tp2.fetch_labeled_frames(max(WINDOWS))
    path = ROOT / "docs" / "backtest_LINK_notrend_hedge_1k_10x_monthly.md"
    cap = 1000.0
    lines = [
        "# Chốt lãi cuối tháng — vốn 1000, 10x, 0.5% cap",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Scale NO_TREND hedge, skip avg lời, TP 2%, đóng khi có trend.",
        "- **Chốt tháng:** đầu mỗi tháng (UTC+7), nếu **MTM tổng lệnh mở > 0** → đóng hết, round mới. "
        "Không lãi → giữ nguyên.",
        "",
        "## So sánh",
        "",
        "| Cửa sổ | Giá | Không chốt tháng | **Chốt tháng** | Δ PnL | Chốt tháng (lần) | Giữ (lần) | Monthly PnL |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for days in WINDOWS:
        m5 = tp2.slice_m5(frames, days)
        first, last = m5.iloc[0], m5.iloc[-1]
        px = (last.close / first.close - 1) * 100
        kw = {"mode": "scale", "capital": cap, "leverage": 10.0, "size_pct": 0.005}
        base_f, base_st = run(m5, **kw, monthly_profit_take=False)
        mon_f, mon_st = run(m5, **kw, monthly_profit_take=True)
        base_eq = base_st["end_equity"]
        mon_eq = mon_st["end_equity"]
        base_pnl = base_eq - cap
        mon_pnl = mon_eq - cap
        lines.append(
            f"| {LABELS[days]} ({tp2._local(int(first.ts))[:10]}→{tp2._local(int(last.ts))[:10]}) | "
            f"{px:+.1f}% | {base_eq:.2f} ({base_pnl:+.1f}) | **{mon_eq:.2f} ({mon_pnl:+.1f})** | "
            f"{mon_pnl - base_pnl:+.1f} | {mon_st['monthly_closes']} | {mon_st['monthly_skips']} | "
            f"{mon_st['monthly_pnl']:+.1f} |"
        )
        print(
            f"{days}d BASE={base_eq:.2f} ({base_pnl:+.1f}) MONTHLY={mon_eq:.2f} ({mon_pnl:+.1f}) "
            f"delta={mon_pnl-base_pnl:+.1f} closes={mon_st['monthly_closes']} skips={mon_st['monthly_skips']} "
            f"month_pnl={mon_st['monthly_pnl']:+.1f} dd_base={base_st['max_dd']:+.0f} dd_mon={mon_st['max_dd']:+.0f}",
            flush=True,
        )
    lines += [
        "",
        "## Chi tiết chốt tháng (scale)",
        "",
        "| Cửa sổ | Vốn cuối | PnL % | Max DD | Equity min | Peak lot | Phí | Liq |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for days in WINDOWS:
        m5 = tp2.slice_m5(frames, days)
        mon_f, mon_st = run(
            m5, mode="scale", capital=cap, leverage=10.0, size_pct=0.005, monthly_profit_take=True
        )
        s = _summ(mon_f)
        pnl = mon_st["end_equity"] - cap
        lines.append(
            f"| {LABELS[days]} | **{mon_st['end_equity']:.2f}** | {pnl / cap * 100:+.1f}% | "
            f"{mon_st['max_dd']:+.0f} | {mon_st['min_equity']:.2f} | {mon_st['max_both']} | "
            f"{s['fee']:.2f} | {'yes' if mon_st['liquidated'] else 'no'} |"
        )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


def main_tp_compare() -> None:
    """So sánh TP 2% vs TP 1% — scale 1k/10x/0.5% cap."""
    frames = tp2.fetch_labeled_frames(max(WINDOWS))
    path = ROOT / "docs" / "backtest_LINK_notrend_hedge_1k_10x_tp1pct.md"
    cap = 1000.0
    lines = [
        "# TP 1% vs 2% — NO_TREND hedge scale, 1000 USDT, 10x, 0.5% cap",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Scale skip avg lời, đóng khi 3 khung có trend.",
        "",
        "## So sánh",
        "",
        "| Cửa sổ | Giá | TP 2% vốn | **TP 1% vốn** | Δ | TP 2% (n) | TP 1% (n) | Trend PnL 2% | Trend PnL 1% | Phí 1% | DD 1% |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for days in WINDOWS:
        m5 = tp2.slice_m5(frames, days)
        first, last = m5.iloc[0], m5.iloc[-1]
        px = (last.close / first.close - 1) * 100
        kw = {"mode": "scale", "capital": cap, "leverage": 10.0, "size_pct": 0.005}
        f2, st2 = run(m5, **kw, tp_pct=0.02)
        f1, st1 = run(m5, **kw, tp_pct=0.01)
        s2, s1 = _summ(f2), _summ(f1)
        eq2, eq1 = st2["end_equity"], st1["end_equity"]
        p2, p1 = eq2 - cap, eq1 - cap
        lines.append(
            f"| {LABELS[days]} ({tp2._local(int(first.ts))[:10]}→{tp2._local(int(last.ts))[:10]}) | "
            f"{px:+.1f}% | {eq2:.2f} ({p2:+.0f}) | **{eq1:.2f} ({p1:+.0f})** | {p1 - p2:+.0f} | "
            f"{s2['tp']} | {s1['tp']} | {s2['pnl_trend']:+.0f} | {s1['pnl_trend']:+.0f} | "
            f"{s1['fee']:.1f} | {st1['max_dd']:+.0f} |"
        )
        print(
            f"{days}d TP2%={eq2:.2f} ({p2:+.1f}) tp={s2['tp']} trend={s2['pnl_trend']:+.0f} | "
            f"TP1%={eq1:.2f} ({p1:+.1f}) tp={s1['tp']} trend={s1['pnl_trend']:+.0f} fee={s1['fee']:.1f} "
            f"dd={st1['max_dd']:+.0f} peak={st1['max_both']}",
            flush=True,
        )
    lines += [
        "",
        "## Chi tiết TP 1%",
        "",
        "| Cửa sổ | Vốn cuối | PnL % | Peak lot | Peak notional | Equity min | Liq |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for days in WINDOWS:
        m5 = tp2.slice_m5(frames, days)
        f1, st1 = run(
            m5, mode="scale", capital=cap, leverage=10.0, size_pct=0.005, tp_pct=0.01
        )
        pnl = st1["end_equity"] - cap
        lines.append(
            f"| {LABELS[days]} | **{st1['end_equity']:.2f}** | {pnl / cap * 100:+.1f}% | "
            f"{st1['max_both']} | {st1['max_notional']:.0f} | {st1['min_equity']:.2f} | "
            f"{'yes' if st1['liquidated'] else 'no'} |"
        )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main_tp_compare()

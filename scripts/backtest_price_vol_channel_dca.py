#!/usr/bin/env python3
"""Config A + DCA-on-loss variants — feasibility study (paper).

Baseline = live Config A (no hard SL, TP = channel band).
DCA: khi lệnh đang lỗ, thêm 1 lần (cùng side), gộp avg entry; vẫn thoát khi chạm TP band.

Variants:
  baseline     — no DCA
  dca_opp_1x   — chạm opp_band lúc vào → add 1× margin gốc (max 1 add)
  dca_opp_0.5x — add 0.5× margin
  dca_1R_1x    — adverse ≥ 1× |entry−opp| @ close → add 1×
  dca_opp_1x_stop2R — như opp_1x nhưng hard stop nếu adverse ≥ 2R sau khi đã DCA

Usage:
  .venv/bin/python scripts/backtest_price_vol_channel_dca.py
  .venv/bin/python scripts/backtest_price_vol_channel_dca.py --windows 365,1095
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DOCS = ROOT / "docs"

spec = importlib.util.spec_from_file_location("pvc", ROOT / "scripts/backtest_price_vol_channel.py")
pvc = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["pvc"] = pvc
spec.loader.exec_module(pvc)

channel_enrich = pvc.channel_enrich
load_pool = pvc.load_pool
CAPITAL = pvc.CAPITAL
LEVERAGE = pvc.LEVERAGE
pnl = pvc.pnl


@dataclass
class DcaCfg:
    name: str
    mode: str  # none | opp | risk
    add_mult: float = 1.0  # vs original margin
    trigger_r: float = 1.0  # for mode=risk
    max_adds: int = 1
    hard_stop_r: float | None = None  # after any adds, stop if adverse >= this × initial R
    margin_pct: float = 0.01
    size_mult_cap: float = 2.0
    min_vol: float = 1.2
    min_pot_rr: float = 0.5
    max_open: int = 10
    top_k: int = 5
    body_lo: float = 0.3
    body_hi: float = 1.2


def run_dca(cfg: DcaCfg, raw_dfs: dict, eval_start: int) -> dict:
    dfs = {sym: channel_enrich(df, 20, 5, 0.015) for sym, df in raw_dfs.items()}
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)
    first_eval = next((t for t in common if t >= eval_start), common[-1])
    days = max((common[-1] - first_eval) / 86400000.0, 1e-9)

    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}
    peak = CAPITAL
    maxdd = 0.0
    dca_count = 0
    dca_stop_exits = 0

    def equity(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def close_pos(t: dict, px: float, reason: str, ts: int) -> None:
        nonlocal cash
        pl = pnl(t["side"], t["entry"], px, t["qty"])
        cash += t["margin"] + pl
        if ts >= eval_start:
            trades.append(
                {
                    "pnl": pl,
                    "reason": reason,
                    "side": t["side"],
                    "sym": t["sym"],
                    "adds": t.get("adds", 0),
                }
            )

    def try_dca(t: dict, px: float, hi: float, lo: float) -> None:
        nonlocal cash, dca_count
        if cfg.mode == "none" or t.get("adds", 0) >= cfg.max_adds:
            return
        r0 = t["r0"]
        if r0 <= 0:
            return
        side = t["side"]
        if side == "long":
            adverse = t["entry"] - px  # use close for risk mode; band for opp
            band_hit = lo <= t["sl0"]
        else:
            adverse = px - t["entry"]
            band_hit = hi >= t["sl0"]

        trigger = False
        if cfg.mode == "opp":
            trigger = band_hit
        elif cfg.mode == "risk":
            trigger = adverse >= cfg.trigger_r * r0

        if not trigger:
            return
        add_margin = min(t["base_margin"] * cfg.add_mult, cash)
        if add_margin < 1.0:
            return
        add_qty = add_margin * LEVERAGE / px
        new_qty = t["qty"] + add_qty
        t["entry"] = (t["entry"] * t["qty"] + px * add_qty) / new_qty
        t["qty"] = new_qty
        t["margin"] += add_margin
        cash -= add_margin
        t["adds"] = t.get("adds", 0) + 1
        dca_count += 1

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

        still = []
        for t in opens:
            b = bar[t["sym"]]
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["ch_upper"]), float(b["ch_lower"])
            side = t["side"]
            tp = up if side == "long" else dn
            px = float(b["close"])

            # TP first
            if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                close_pos(t, tp, "TP", ts)
                continue

            # hard stop after DCA (optional)
            if cfg.hard_stop_r is not None and t.get("adds", 0) > 0:
                r0 = t["r0"]
                if side == "long":
                    adverse = t["orig_entry"] - lo  # worst of bar vs original entry
                    adverse_c = t["orig_entry"] - px
                else:
                    adverse = hi - t["orig_entry"]
                    adverse_c = px - t["orig_entry"]
                if max(adverse, adverse_c) >= cfg.hard_stop_r * r0:
                    stop_px = t["orig_entry"] - cfg.hard_stop_r * r0 if side == "long" else t["orig_entry"] + cfg.hard_stop_r * r0
                    close_pos(t, stop_px, "DCA_STOP", ts)
                    dca_stop_exits += 1
                    continue

            # DCA before carrying
            if ts >= eval_start:
                try_dca(t, px, hi, lo)
            still.append(t)
        opens = still

        if ts < eval_start:
            for sym in symbols:
                b = bar[sym]
                if bool(b.get("channel_expand", False)):
                    px = float(b["close"])
                    mid = float(b["ch_mid"])
                    state[sym]["trend"] = "up" if px > mid else "down"
                    state[sym]["waiting"] = True
            continue

        cands: list[dict] = []
        for sym in symbols:
            b = bar[sym]
            if np.isnan(b["ch_upper"]) or np.isnan(b["atr"]) or np.isnan(b.get("vol_ratio", np.nan)):
                continue
            px, o = float(b["close"]), float(b["open"])
            up, dn, mid = float(b["ch_upper"]), float(b["ch_lower"]), float(b["ch_mid"])
            a = float(b["atr"])
            st = state[sym]
            if bool(b.get("channel_expand", False)):
                st["trend"] = "up" if px > mid else "down"
                st["waiting"] = True
            if not st["waiting"] or not st["trend"]:
                continue
            if any(t["sym"] == sym for t in opens):
                continue
            is_green, is_red = px > o, px < o
            counter = (st["trend"] == "up" and is_red) or (st["trend"] == "down" and is_green)
            if not counter:
                continue
            if bool(b.get("bands_parallel", False)):
                continue
            if float(b["vol_ratio"]) < cfg.min_vol:
                continue
            body = abs(px - o) / a if a > 0 else 0
            if not (cfg.body_lo <= body <= cfg.body_hi):
                continue
            side = "long" if st["trend"] == "up" else "short"
            tp_near = up if side == "long" else dn
            sl_opp = dn if side == "long" else up
            pot_rr = abs(tp_near - px) / max(abs(px - sl_opp), 1e-12)
            if pot_rr < cfg.min_pot_rr:
                continue
            cands.append({"sym": sym, "side": side, "entry": px, "sl0": sl_opp, "pot_rr": pot_rr})

        cands.sort(key=lambda x: x["pot_rr"], reverse=True)
        for cand in cands[: cfg.top_k]:
            if len(opens) >= cfg.max_open:
                break
            if any(t["sym"] == cand["sym"] for t in opens):
                continue
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            mult = min(cand["pot_rr"], cfg.size_mult_cap)
            margin = min(eq * cfg.margin_pct * mult, eq * 0.15, cash)
            if margin < 1.0:
                continue
            qty = margin * LEVERAGE / cand["entry"]
            cash -= margin
            r0 = abs(cand["entry"] - cand["sl0"])
            opens.append(
                {
                    **cand,
                    "qty": qty,
                    "margin": margin,
                    "base_margin": margin,
                    "entry_ts": ts,
                    "orig_entry": cand["entry"],
                    "r0": r0,
                    "adds": 0,
                }
            )
            state[cand["sym"]]["waiting"] = False

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    with_adds = [t for t in trades if t.get("adds", 0) > 0]
    return {
        "name": cfg.name,
        "days": days,
        "n_syms": len(symbols),
        "n": len(trades),
        "wr": len(wins) / len(trades) * 100 if trades else 0,
        "pf": gw / gl if gl > 0 else float("inf"),
        "ret_pct": net / CAPITAL * 100,
        "pct_day": net / CAPITAL * 100 / days,
        "maxdd": maxdd * 100,
        "tpd": len(trades) / days,
        "final_eq": cash,
        "dca_adds": dca_count,
        "dca_trades": len(with_adds),
        "dca_stop_exits": dca_stop_exits,
        "dca_trades_net": sum(t["pnl"] for t in with_adds),
        "dca_trades_wr": (
            100 * sum(1 for t in with_adds if t["pnl"] > 0) / len(with_adds) if with_adds else 0.0
        ),
    }


VARIANTS = [
    DcaCfg("A baseline", mode="none"),
    DcaCfg("DCA @opp ×1", mode="opp", add_mult=1.0),
    DcaCfg("DCA @opp ×0.5", mode="opp", add_mult=0.5),
    DcaCfg("DCA @1R ×1", mode="risk", trigger_r=1.0, add_mult=1.0),
    DcaCfg("DCA @opp ×1 + stop2R", mode="opp", add_mult=1.0, hard_stop_r=2.0),
    DcaCfg("DCA @1R ×0.5 + stop2R", mode="risk", trigger_r=1.0, add_mult=0.5, hard_stop_r=2.0),
]


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default="365,1095")
    args = parser.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Config A — DCA trên lệnh lỗ (feasibility)",
        "",
        f"- Generated: **{now}**",
        "- Script: `scripts/backtest_price_vol_channel_dca.py`",
        "- Baseline = Config A (vol≥1.2, TP band, size_mult=min(pot_rr,2), max10)",
        "- DCA: thêm **tối đa 1 lần**, gộp avg entry; vẫn TP khi chạm band Donchian hiện tại",
        "- `@opp` = giá chạm opp_band lúc vào lệnh · `@1R` = adverse @ close ≥ 1×|entry−opp|",
        "- `stop2R` = sau khi đã DCA, cắt nếu adverse ≥ 2× R gốc (so orig entry)",
        "",
    ]

    for days in windows:
        print(f"\n{'='*70}\nWINDOW {days}d\n{'='*70}", flush=True)
        raw, eval_start, eval_end = load_pool(days)
        print(f"Eval: {fmt_ts(eval_start)} → {fmt_ts(eval_end)}", flush=True)
        rows = []
        for c in VARIANTS:
            print(f"  run {c.name}...", flush=True)
            r = run_dca(c, raw, eval_start)
            rows.append(r)
            print(
                f"    ret={r['ret_pct']:+.1f}% PF={r['pf']:.2f} WR={r['wr']:.1f}% "
                f"MaxDD={r['maxdd']:.1f}% dca_adds={r['dca_adds']} "
                f"dca_trades WR={r['dca_trades_wr']:.0f}% net={r['dca_trades_net']:+.0f}",
                flush=True,
            )

        base = rows[0]
        lines += [
            f"## {days}d · thực tế **{rows[0]['days']:.0f}d** · **{rows[0]['n_syms']} coin** "
            f"({fmt_ts(eval_start)} → {fmt_ts(eval_end)})",
            "",
            "| Variant | Return | PF | WR | MaxDD | ΔPF | ΔMaxDD | DCA adds | Trades có DCA | WR DCA | Net DCA | Final eq |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for r in rows:
            lines.append(
                f"| {r['name']} | {r['ret_pct']:+.1f}% | {r['pf']:.2f} | {r['wr']:.1f}% | {r['maxdd']:.1f}% | "
                f"{r['pf'] - base['pf']:+.2f} | {r['maxdd'] - base['maxdd']:+.1f}pp | "
                f"{r['dca_adds']} | {r['dca_trades']} | {r['dca_trades_wr']:.0f}% | "
                f"{r['dca_trades_net']:+.0f} | {r['final_eq']:.0f} |"
            )
        lines.append("")

    lines += [
        "## Đọc nhanh / khả thi?",
        "",
        "- DCA **khả thi kỹ thuật** (gộp lệnh, thêm margin) nhưng chỉ nên cân nhắc nếu **PF ↑ hoặc MaxDD ↓** vs baseline.",
        "- Nếu Net DCA âm / WR DCA thấp → đang **đổ thêm tiền vào xu hướng sai** (phù hợp live: lỗ nặng = hold lâu + size lớn).",
        "- Live risk: maint tăng, margin bị khóa, max_open/cash cạn khi nhiều DCA cùng lúc.",
        "",
    ]
    out = DOCS / "backtest_channel_vol_A_dca.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
